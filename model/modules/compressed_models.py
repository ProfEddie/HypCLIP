
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional
from transformers import (
    BlipConfig, 
)
import math
from lavis import BlipRetrieval, Blip2Qformer
from transformers import AutoModel 
from .dct import dct, idct


class CompressedModel(nn.Module):
    def __init__(self, compress_method='dct', r=0.95, window_size=2):
        super().__init__()
        self.r = r
        self.window_size=window_size
        self.compress_method = compress_method
        self.num_reduced_token = self.window_size * 15 
    

    def std_filter_with_r(self, x, use_mean=False, k = 2):        
        B, T, D = x.shape
        with torch.no_grad():
            if k is None:
                k = math.floor((T- T*self.r)/self.window_size)
            batch_idx = torch.arange(x.shape[0]).unsqueeze(1)

            first_x = x[:,:(T%self.window_size),:]
            remain_x = x[:,(T%self.window_size):,:]
            remain_x = remain_x.view(B, -1, self.window_size, D)
            std_array = remain_x.std(-1)
            max_std = std_array.max(-1)[0] 
       
            _, min_indices = torch.topk(max_std, k=k, dim=-1, largest=False)
            mask_to_keep = torch.ones_like(remain_x, dtype=torch.bool)
            mask_to_keep[batch_idx, min_indices, :, :] = False
            min_std_array = std_array[batch_idx, min_indices, :] 

            filtered_tensor = torch.masked_select(remain_x, mask_to_keep).view(remain_x.size(0), -1, remain_x.size(2), remain_x.size(3))

            if not use_mean:
                # min_std_array = F.softmax(min_std_array, dim=-1)
                # reduced_tensor = min_std_array.unsqueeze(3).transpose(-1,-2) @ remain_x[batch_idx, min_indices, :, :]
                # reduced_tensor = remain_x[batch_idx, min_indices, :, :].mean(dim=2, keepdim=True)
                reduced_tensor = remain_x[batch_idx, min_indices, :, :]
                # min_std_array = F.softmax(reduced_tensor.std(-1), dim=-1).view(B,-1)
                min_std_array = reduced_tensor.std(-1).view(B,-1)
                min_std_array = (min_std_array - min_std_array.min(-1, keepdim=True)[0])/min_std_array.min(-1, keepdim=True)[0]
                # print(min_std_array.shape)
                # min_std_array = F.softmax(reduced_tensor.std(-1), dim=-1).view(B,-1).unsqueeze(2)
                reduced_tensor = min_std_array.unsqueeze_(2).transpose(-1,-2) @ reduced_tensor.view(B,-1,D)
                output = torch.cat([first_x, filtered_tensor.view(B,-1,D), reduced_tensor], dim=1)
            else:
                reduced_tensor = remain_x[batch_idx, min_indices, :, :].mean(dim=2, keepdim=True)
                output = torch.cat([first_x, filtered_tensor.view(B,-1,D), reduced_tensor.view(B,-1,D).mean(dim=1, keepdim=True)], dim=1)
      
        return output, None 
    
    # def std_filter_with_r(self, x:torch.tensor, use_mean=False):        
    #     B, T, D = x.shape
    #     with torch.no_grad():
    #         k = math.floor(T- T*self.r)
    #         std_array = x.std(-1)
       
    #         min_std_array , min_indices = torch.topk(std_array, k=k, dim=-1, largest=False)
    #         mask_to_keep = torch.ones_like(x, dtype=torch.bool)
    #         mask_to_keep[torch.arange(x.size(0)).unsqueeze(1), min_indices, :] = False
    #         filtered_tensor = torch.masked_select(x, mask_to_keep).view(x.size(0), -1, x.size(2))
    #         if not use_mean:
    #             min_std_array = min_std_array - min_std_array.min(-1)[0].unsqueeze(1)
    #             min_std_array = min_std_array/min_std_array.max(-1)[0].unsqueeze(1)
    #             reduced_tensor = min_std_array.unsqueeze(2).permute(0,2,1) @ x[torch.arange(x.size(0)).unsqueeze(1), min_indices, :]
    #         else:
    #             reduced_tensor = x[torch.arange(x.size(0)).unsqueeze(1), min_indices, :].mean(dim=1, keepdim=True)
    #         output = torch.cat([filtered_tensor, reduced_tensor], dim=1)
      
    #     return output, None 

    def bipartite_soft_matching(
        self,
        x: torch.Tensor,
        r: int=None,
    ):
        T = x.shape[1]

        protected = 0
        if r is None:
            r = math.floor(T- T*self.r)
            # print(r)
    
        # We can only reduce by a maximum of 50% tokens
        r = min(r, (T - protected) // 2)

        if r <= 0:
            return x, x

        with torch.no_grad():
            x = F.normalize(x, p=2, dim=-1) 
            a, b = x[..., ::2, :], x[..., 1::2, :]
            scores = a @ b.transpose(-1, -2)

       

            node_max, node_idx = scores.max(dim=-1)
            edge_idx = node_max.argsort(dim=-1, descending=True)[..., None]
            # print(node_max)

            unm_idx = edge_idx[..., r:, :]  # Unmerged Tokens
            src_idx = edge_idx[..., :r, :]  # Merged Tokens
            dst_idx = node_idx[..., None].gather(dim=-2, index=src_idx)


        def merge(x: torch.Tensor, mode="mean") -> torch.Tensor:
            src, dst = x[..., ::2, :], x[..., 1::2, :]
            n, t1, c = src.shape
            unm = src.gather(dim=-2, index=unm_idx.expand(n, t1 - r, c))
            src = src.gather(dim=-2, index=src_idx.expand(n, r, c))
            dst = dst.scatter_reduce(-2, dst_idx.expand(n, r, c), src, reduce=mode)

            return torch.cat([unm, dst], dim=1)

       

        return merge


    def merge_wavg(
        self, merge, x: torch.Tensor, size: torch.Tensor = None
    ): 
        """
        Applies the merge function by taking a weighted average based on token size.
        Returns the merged tensor and the new token sizes.
        """
        if size is None:
            size = torch.ones_like(x[..., 0, None])

        x = merge(x * size, mode="sum")
        size = merge(size, mode="sum")

        x = x / size
        return x, None 
            
    def random_filter_with_r(self, x, use_mean=False, k = 2):        
        B, T, D = x.shape
        with torch.no_grad():
            if k is None:
                k = math.floor((T- T*self.r)/self.window_size)
            batch_idx = torch.arange(x.shape[0]).unsqueeze(1)

            first_x = x[:,:(T%self.window_size),:]
            remain_x = x[:,(T%self.window_size):,:]
            remain_x = remain_x.view(B, -1, self.window_size, D)
            std_array = remain_x.std(-1)
            max_std = std_array.max(-1)[0] 
       
            min_indices = torch.randint(0, remain_x.shape[1], (1, k)).squeeze(0)
            mask_to_keep = torch.ones_like(remain_x, dtype=torch.bool)
            mask_to_keep[batch_idx, min_indices, :, :] = False
            min_std_array = std_array[batch_idx, min_indices, :] 

            filtered_tensor = torch.masked_select(remain_x, mask_to_keep).view(remain_x.size(0), -1, remain_x.size(2), remain_x.size(3))

            if not use_mean:
                # min_std_array = F.softmax(min_std_array, dim=-1)
                # reduced_tensor = min_std_array.unsqueeze(3).transpose(-1,-2) @ remain_x[batch_idx, min_indices, :, :]
                reduced_tensor = remain_x[batch_idx, min_indices, :, :].mean(dim=2, keepdim=True)
                min_std_array = F.softmax(reduced_tensor.std(-1), dim=-1)
                reduced_tensor = min_std_array.transpose(-1,-2) @ reduced_tensor.view(B,-1,D)
                # print(reduced_tensor.shape)
                output = torch.cat([first_x, filtered_tensor.view(B,-1,D), reduced_tensor.view(B,-1,D)], dim=1)
            else:
                reduced_tensor = remain_x[batch_idx, min_indices, :, :].mean(dim=2, keepdim=True)
                output = torch.cat([first_x, filtered_tensor.view(B,-1,D), reduced_tensor.view(B,-1,D).mean(dim=1, keepdim=True)], dim=1)
      
        return output, None 
    
    
    
    def forward(
        self,
        input_ids: torch.LongTensor=None,
        pixel_values: torch.FloatTensor=None,
        attention_mask: Optional[torch.LongTensor] = None,
        use_compressed_hidden_state: Optional[torch.LongTensor] = True,
        
    ):
        if input_ids is not None:
            return self.get_text_features(input_ids=input_ids, attention_mask=attention_mask)
        else:
            return self.get_vision_features(pixel_values=pixel_values, use_compressed_hidden_state=use_compressed_hidden_state)

    def dc_transform(self, x, use_reconstucted_state=False, threshold=None):
        # cufft doesn't accept fp16
        x = x.permute(1,0,2)
        x_dct = dct(x.transpose(0,2), norm='ortho').transpose(0,2)
        T, B, C = x_dct.size()
        k = math.ceil(self.r * T)

        if use_reconstucted_state:
            x_dct = x_dct[:k, :, :]
            x = idct(x_dct.transpose(0,2), norm='ortho').transpose(0,2)
            # print(x)
   
        return x.permute(1,0,2), x_dct.permute(1,0,2)

    def direct(self, x, use_reconstucted_state = False):
        k = math.ceil(0.90 * x.shape[1])
        if use_reconstucted_state:
            x = x[:,:k,:]  
        return x, x
    
    def std_based_compress(self, x, use_reconstucted_state, threshold=0.7,filter_strategy='std'):
        if use_reconstucted_state:
            x = self.std_filter(x, threshold, filter_strategy=filter_strategy) 
        return x, x
   
    def get_vision_features(self, pixel_values, use_compressed_hidden_state=True, return_all_hidden_state=False):
        raise NotImplementedError("This method is not implemented yet")

    def get_text_features(self, input_ids, attention_mask):
        raise NotImplementedError("This method is not implemented yet")
    
    def compress_hidden_state(self, x, use_compressed_hidden_state, use_mean=False):
        if self.compress_method == 'dct':
            x_reconstructed, energy = self.dc_transform(x ,use_compressed_hidden_state ) 
        elif self.compress_method == 'random-mean-merge':
            x_reconstructed, energy = self.random_filter_with_r(x, k=self.num_reduced_token//self.window_size, use_mean=True) 
        elif self.compress_method == 'random-std-merge':
            x_reconstructed, energy = self.random_filter_with_r(x, k=self.num_reduced_token//self.window_size, use_mean=False) 
        elif self.compress_method == 'std-weighted-merge':
            x_reconstructed, energy = self.std_filter_with_r(x, use_mean=use_mean, k=self.num_reduced_token//self.window_size) 
        elif self.compress_method == 'std-mean-merge':
            x_reconstructed, energy = self.std_filter_with_r(x, use_mean=True,  k=self.num_reduced_token//self.window_size) 
        elif self.compress_method == 'bipartite-soft-matching':
            merge = self.bipartite_soft_matching(x, self.num_reduced_token) 
            x_reconstructed, energy = self.merge_wavg(merge, x) 
        else: 
            return x, x

        return  x_reconstructed, energy

    
class CompressedHFBLIP(CompressedModel):
    config_class = BlipConfig

    def __init__(self, model:AutoModel, compress_method='dct', r=0.9):
        super(CompressedHFBLIP, self).__init__(compress_method, r=r)
        self.vision_model = model.vision_model
        self.text_model = model.text_model 
        self.vision_proj = model.visual_projection 
        self.text_proj = model.text_projection 
        self.compress_layers = [6, 7, 8]
     

    
    def get_vision_features(self, pixel_values, use_compressed_hidden_state=True, return_all_hidden_state=False):
        hidden_states = self.vision_model.embeddings(pixel_values)
        all_hidden_states = []
        energy = []
        real_mem = 0
        total_mem = 0
        ori_size = hidden_states.shape[1]

        for i, layer in enumerate(self.vision_model.encoder.layers):
            if i in self.compress_layers:    
                cls = hidden_states[:, 0, :].unsqueeze(1)
                state, cur_energy = self.compress_hidden_state(
                    hidden_states[:, 1:, :], 
                    use_compressed_hidden_state=use_compressed_hidden_state,
                    # use_mean=i < len(self.compress_layers)/2
                )
                hidden_states = torch.cat([cls, state], dim=1)
                if return_all_hidden_state or i == len(self.vision_model.encoder.layers)-1:
                    energy.append(cur_energy)
                    all_hidden_states.append(hidden_states)
                real_mem += hidden_states.shape[1]
                total_mem += ori_size 

            hidden_states = layer(
                hidden_states,
                None,
                None
            )[0]


        last_hidden_state = self.vision_model.post_layernorm(hidden_states)
        pooled_output = last_hidden_state[:, 0, :]
        vision_embed = self.vision_proj(pooled_output)
       
        return hidden_states, vision_embed, all_hidden_states, energy, real_mem/total_mem

    def get_text_features(self, input_ids, attention_mask):
        text_output = self.text_model(
            input_ids=input_ids,
            attention_mask=attention_mask,
        )
        last_hidden_state = text_output[0] 
        text_embed = self.text_proj(text_output[1])

        return  last_hidden_state, text_embed


class CompressedLAVISBLIP(CompressedModel):

    def __init__(self, model:BlipRetrieval, compress_method='dct',r=0.9):
        super(CompressedLAVISBLIP, self).__init__(compress_method, r=r)

        self.vision_model = model.visual_encoder
        self.text_model = model.text_encoder 
        self.vision_proj = model.vision_proj 
        self.text_proj = model.text_proj 
        self.compress_layers = [i for i in range(1,len(self.vision_model.blocks))]
        # self.compress_layers = [1,7]

   
    def get_vision_features(self, pixel_values, use_compressed_hidden_state=True, return_all_hidden_state=False):
        B = pixel_values.shape[0]
        x = self.vision_model.patch_embed(pixel_values)
        hidden_states = []
        energy = [] 
        cls_tokens = self.vision_model.cls_token.expand(
            B, -1, -1
        ) 
        x = torch.cat((cls_tokens, x), dim=1)
        x = x + self.vision_model.pos_embed[:, : x.size(1), :]
        x = self.vision_model.pos_drop(x)
        ori_size = x.shape[1]
        real_mem = 0
        total_mem = 0
        for i, blk in enumerate(self.vision_model.blocks):
            if i in self.compress_layers: 
                cls = x[:, 0, :].unsqueeze(1)
                state, cur_energy = self.compress_hidden_state(
                    x[:, 1:, :], 
                    use_compressed_hidden_state=use_compressed_hidden_state,
                    use_mean=i < len(self.compress_layers)/2
                )
                x = torch.cat([cls, state], dim=1)

                if return_all_hidden_state or i == len(self.vision_model.blocks)-1:
                    energy.append(cur_energy)
                    hidden_states.append(state)
                real_mem += x.shape[1]
                total_mem += ori_size 
            x = blk(x)

        # with torch.no_grad():
        x = self.vision_model.norm(x)
        vision_embed = self.vision_proj(x[:,0,:])
        return x, vision_embed, hidden_states, energy, real_mem/total_mem

    def get_text_features(self, input_ids, attention_mask):
        # with torch.no_grad():
        class Text(object):
            pass
        text = Text() 
        text.input_ids=input_ids
        text.attention_mask=attention_mask
        text_output = self.text_model.forward_text(text)
        last_hidden_state = text_output[0] 
        text_embed = self.text_proj(last_hidden_state[:,0,:])

        return  last_hidden_state, text_embed


class CompressedHFCLIP(CompressedModel):

    def __init__(self, model:AutoModel, compress_method='dct',r=0.9):
        super(CompressedHFCLIP, self).__init__(compress_method, r=r)

        self.vision_model = model.vision_model
        self.text_model = model.text_model 
        self.vision_proj = model.visual_projection 
        self.text_proj = model.text_projection 
        # self.compress_layers = [1, 7, 13, 19] if len(self.vision_model.encoder.layers) > 12 else [1, 7]
        self.compress_layers = [i for i in range(1,len(self.vision_model.encoder.layers))]

    def get_vision_features(self, pixel_values, use_compressed_hidden_state=True, return_all_hidden_state=False):
        energy = []
        all_hidden_states = []
        hidden_states = self.vision_model.embeddings(pixel_values)
        hidden_states = self.vision_model.pre_layrnorm(hidden_states)
        real_mem = 0
        total_mem = 0
        ori_size = hidden_states.shape[1]
        for i, layer in enumerate(self.vision_model.encoder.layers):
            if i in self.compress_layers:
                cls = hidden_states[:, 0, :].unsqueeze(1)
                state, cur_energy = self.compress_hidden_state(
                    hidden_states[:, 1:, :], 
                    use_compressed_hidden_state=use_compressed_hidden_state,
                    use_mean=i < len(self.compress_layers)/2
                )
                hidden_states = torch.cat([cls, state], dim=1)
                # print(hidden_states.shape)
            if return_all_hidden_state or i == len(self.vision_model.encoder.layers)-1:
                energy.append(cur_energy)
                all_hidden_states.append(hidden_states)
            real_mem += hidden_states.shape[1]
            total_mem += ori_size 

            hidden_states = layer(
                hidden_states,
                None,
                None
            )[0]

        last_hidden_state = hidden_states
        pooled_output = last_hidden_state[:, 0, :]
        pooled_output = self.vision_model.post_layernorm(pooled_output)
        vision_embed = self.vision_proj(pooled_output)
        

        return hidden_states, vision_embed, all_hidden_states, energy, real_mem/total_mem

    def get_text_features(self, input_ids, attention_mask):
        text_outputs = self.text_model(
            input_ids=input_ids,
            attention_mask=attention_mask,
        )
        pooled_output = text_outputs[1]
        text_embed = self.text_proj(pooled_output)

        return  text_outputs[0], text_embed

        
class CompressedLAVISBLIP2(CompressedModel):

    def __init__(self, model:Blip2Qformer, compress_method='dct',r=0.9):
        super(CompressedLAVISBLIP2, self).__init__(compress_method,r=r)

        self.ln_vision = model.ln_vision
        self.visual_encoder = model.visual_encoder
        self.query_tokens = model.query_tokens
        self.vision_proj = model.vision_proj
        self.text_proj = model.text_proj
        self.Qformer = model.Qformer
        self.itm_head = model.itm_head
        # self.compress_layers = [20,22,24,26,28,30,32,34,36,38,40]
        
        self.compress_layers = [i for i in range(1,len(self.visual_encoder.blocks))]

   
    def get_vision_features(self, pixel_values:torch.Tensor, use_compressed_hidden_state=True, return_all_hidden_state=False):
        all_hidden_states = []
        energy = []
        total_mem=0
        real_mem=0
        with torch.no_grad():
            x = self.visual_encoder.patch_embed(pixel_values.squeeze(0))
            batch_size, seq_len, _ = x.size()

            cls_tokens = self.visual_encoder.cls_token.expand(batch_size, -1, -1)  # stole cls_tokens impl from Phil Wang, thanks
            x = torch.cat((cls_tokens, x), dim=1)
            if self.visual_encoder.pos_embed is not None:
                x = x + self.visual_encoder.pos_embed
            x = self.visual_encoder.pos_drop(x)
            ori_size = x.shape[1]

            rel_pos_bias = self.visual_encoder.rel_pos_bias() if self.visual_encoder.rel_pos_bias is not None else None
            for i, blk in enumerate(self.visual_encoder.blocks):
                if i in self.compress_layers:
                    x, cur_energy = self.compress_hidden_state(
                        x, 
                        use_compressed_hidden_state=use_compressed_hidden_state,
                        use_mean=i<len(self.compress_layers)/2
                    )
                x = blk(x, rel_pos_bias)
                if return_all_hidden_state or i == len(self.visual_encoder.blocks) - 1:
                    energy.append(cur_energy)
                    all_hidden_states.append(x)
                real_mem += x.shape[1]
                total_mem += ori_size 
            vit_embeds = self.ln_vision(x)



        image_atts = torch.ones(vit_embeds.size()[:-1], dtype=torch.long).to(
            pixel_values.device
        )
        query_tokens = self.query_tokens.expand(vit_embeds.shape[0], -1, -1)
        query_output = self.Qformer.bert(
            query_embeds=query_tokens,
            encoder_hidden_states=vit_embeds,
            encoder_attention_mask=image_atts,
            return_dict=True,
        )
        pooled_output = self.vision_proj(query_output.last_hidden_state)
        # return vit_embeds, pooled_output, all_hidden_states
        return vit_embeds, pooled_output, all_hidden_states, energy, real_mem/total_mem 

    def get_text_features(self, input_ids, attention_mask):
        # with torch.no_grad():
        text_output = self.Qformer.bert(
            input_ids=input_ids.squeeze(),
            attention_mask=attention_mask.squeeze(),
            return_dict=True,
        )

        pooled_output = self.text_proj(text_output.last_hidden_state[:, 0, :])
        return text_output.last_hidden_state, pooled_output