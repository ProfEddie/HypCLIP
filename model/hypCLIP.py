import torch
import torch.nn as nn
from .modules.text_model import CLIPText
from .modules.vision_model import CLIPVision 
from .modules.discriminator import Discriminator as DisModel
from .modules.seq_linear import  LorentzSeqLinear, HypSeqLinear
from .modules.hyp_discriminator import HypDiscriminator as HypDisModel
from .modules.hyp_discriminator import LorentzDiscriminator as LorentzDisModel
from .manifolds.euclidean import Euclidean 
from .manifolds.hyperboloid import Hyperboloid 
from .manifolds.lorentz import Lorentz 
from .manifolds.poincare import PoincareBall 
from transformers import CLIPTextModel, CLIPVisionModel 
from typing import  Optional, Tuple, Union
from transformers.models.clip.modeling_clip import CLIPOutput
import torch.nn.functional as F
from .modules.utils import ManifoldMapper 


EUCLID = 'euclidean'
POINCARE = 'poincare'
LORENTZ = 'lorentz'


class HypCLIP(nn.Module):
    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        

        self.model_ckt = config.model_ckt
        self.ft_out = config.ft_out
        self.clip_r = config.clip_radius
        self.device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')

        text_body = CLIPTextModel.from_pretrained(self.model_ckt, cache_dir=config.cache_dir) 
        vision_body = CLIPVisionModel.from_pretrained(self.model_ckt, cache_dir=config.cache_dir) 

        
  


        manifold = config.manifold
        assert manifold in [EUCLID, POINCARE, LORENTZ]

        self.temp = nn.Parameter(torch.as_tensor(config.temp), requires_grad=config.temp != 0) 

        self.curv = torch.as_tensor(config.curv if manifold != EUCLID else 0)
        if not torch.is_floating_point(self.curv):
            self.curv = self.curv.to(torch.get_default_dtype())
    

        if manifold == EUCLID:
            self.curv = torch.nn.Parameter(self.curv, requires_grad=False)
            self.clip_r = None
            self.manifold = Euclidean()
            self.discriminator = DisModel(dim=config.ft_out)
        elif manifold == POINCARE:
            self.curv = torch.nn.Parameter(self.curv, requires_grad=config.curv_learnable)
            self.manifold = PoincareBall()
            self.discriminator = HypDisModel(self.manifold, c=self.curv ,dim=config.ft_out)
        else: 
            self.curv = torch.nn.Parameter(self.curv, requires_grad=config.curv_learnable)
            self.manifold = Lorentz(k=self.curv, learnable=config.curv_learnable)
            self.discriminator = LorentzDisModel(self.manifold, c=self.curv ,dim=config.ft_out)
        self.manifold_name = manifold

        text_head = nn.ModuleList([ManifoldMapper(self.manifold, curv=self.curv, clip_r=self.clip_r)])
        vision_head = nn.ModuleList([ManifoldMapper(self.manifold, curv=self.curv, clip_r=self.clip_r)])

        if manifold == EUCLID:
            text_head.append(nn.Linear(text_body.config.hidden_size, self.ft_out, bias=False))
            vision_head.append(nn.Linear(vision_body.config.hidden_size, self.ft_out, bias=False))
        elif manifold == LORENTZ: 
            text_head.append(LorentzSeqLinear(manifold=self.manifold, ft_in=text_body.config.hidden_size, layer_dims=[self.ft_out]))
            vision_head.append(LorentzSeqLinear(manifold=self.manifold, ft_in=vision_body.config.hidden_size, layer_dims=[self.ft_out]))
        else: 
            text_head.append(HypSeqLinear(manifold=self.manifold, c=self.curv ,ft_in=text_body.config.hidden_size, layer_dims=[self.ft_out]))
            vision_head.append(HypSeqLinear(manifold=self.manifold, c=self.curv ,ft_in=vision_body.config.hidden_size, layer_dims=[self.ft_out]))

        self.vision_model = CLIPVision(body=vision_body, head=vision_head, num_trainable_blocks=config.vision_trainable_blocks, freeze_embedding=config.freeze_embedding)
        self.text_model = CLIPText(body=text_body, head=text_head, num_trainable_blocks=config.text_trainable_blocks, freeze_embeddings=config.freeze_embedding)

    def num_parameters(self, only_trainable=True):
        num_params = 0
        num_params += self.vision_model.body.num_parameters(only_trainable=only_trainable)
        num_params += self.text_model.body.num_parameters(only_trainable=only_trainable)
        vision_head = self.vision_model.head
        text_head = self.text_model.head
        if only_trainable:
            num_params += sum(p.numel() for p in text_head.parameters() if p.requires_grad)
            num_params += sum(p.numel() for p in vision_head.parameters() if p.requires_grad)
            num_params += sum(p.numel() for p in self.discriminator.parameters() if p.requires_grad)
            num_params += int(self.curv.requires_grad)
            num_params += int(self.temp.requires_grad)
        else:
            num_params += sum(p.numel() for p in text_head.parameters())
            num_params += sum(p.numel() for p in vision_head.parameters())
            num_params += sum(p.numel() for p in self.discriminator.parameters())
            num_params += 2 
        return num_params

    def eval(self):
        self.vision_model.body.eval()
        self.vision_model.head.eval()
        self.text_model.body.eval()
        self.text_model.head.eval()

    def train(self):
        self.vision_model.body.train()
        self.vision_model.head.train()
        self.text_model.body.train()
        self.text_model.head.train()
    

        
    def dist_func(self, text_embeds, image_embeds):

        if self.manifold_name == EUCLID:
            print('calulating dot product')
            image_embeds = F.normalize(image_embeds,p=2, dim=-1) 
            text_embeds = F.normalize(text_embeds,p=2, dim=-1) 
            return torch.matmul(text_embeds, image_embeds.t()) 
        elif self.manifold_name == LORENTZ:
            print('calculating lorentz distance')
            return -self.manifold.sqdist_batch(text_embeds, image_embeds)
        else:
            print('calculating poincare distance')
            return -self.manifold.sqdist_batch(text_embeds, image_embeds, c=self.curv)




    def itm_loss(self, imgs, cap, sims_i2t):
  
        bs = imgs.shape[0]
        weights_i2t = F.softmax(sims_i2t, dim=1)
        weights_t2i = F.softmax(sims_i2t.T, dim=1)
        mask = (torch.eye(bs)>1).to(self.device)

        weights_i2t.masked_fill_(mask, 0)
        weights_t2i.masked_fill_(mask, 0) 
        # select a negative image for each text
        img_enc_neg = []    
        for b in range(bs):
            neg_idx = torch.multinomial(weights_t2i[b], 1).item()
            img_enc_neg.append(imgs[neg_idx])
        img_enc_neg = torch.stack(img_enc_neg,dim=0) 

        # select a negative text for each image
        cap_enc_neg = []
        for b in range(bs):
            neg_idx = torch.multinomial(weights_i2t[b], 1).item()
            cap_enc_neg.append(cap[neg_idx])
        cap_enc_neg = torch.stack(cap_enc_neg,dim=0)   

        cap_enc_all = torch.cat([cap, cap, cap_enc_neg],dim=0)     
        img_enc_all = torch.cat([imgs, img_enc_neg, imgs],dim=0)
        itm_labels = torch.cat(
            [
                torch.ones(bs,dtype=torch.float),
                torch.zeros(2*bs,dtype=torch.float)
            ],
        dim=0).view(-1,1).to(imgs.device)

        disc = self.discriminator(img_enc_all, cap_enc_all)
        loss_itm = F.binary_cross_entropy_with_logits(disc, itm_labels)
        return loss_itm

    def lorentz_contrastive_loss(self, sims_i2t, sims_i2i):
        bsize = sims_i2t.shape[0] 
        ones = torch.ones(bsize, bsize).to(self.device)

        pos_mask = torch.eye(bsize).to(self.device) 
        neg_mask = torch.ne(ones, pos_mask).float().to(self.device)

        neg_margin = self.config.lorentz_neg_margin * neg_mask 
        pos_margin = self.config.lorentz_pos_margin * pos_mask 

        sims_i2t = sims_i2t + neg_margin 
        sims_i2i = sims_i2i + neg_margin 
        sims_i2t = (sims_i2t + pos_margin) * ones.masked_fill_(torch.eq(ones, pos_mask), -1.0)
        sims = torch.cat([torch.clamp(sims_i2t, min=0.0) , torch.clamp(sims_i2i, min=0.0)], dim=-1) 
        loss =  torch.mean(torch.sum(sims.pow(2),dim=-1), dim=0) 
        return loss
        

    def euclid_contrastive_loss(self, sims_i2t, sims_i2i):
        bsize = sims_i2t.shape[0] 
        ones = torch.ones(bsize, bsize).to(self.device)

        pos_mask = torch.eye(bsize).to(self.device) 
        neg_mask = torch.ne(ones, pos_mask).float().to(self.device)

        neg_margin = self.config.euclid_neg_margin * neg_mask 
        pos_margin = self.config.euclid_pos_margin * pos_mask 

        sims_i2t = sims_i2t - neg_margin 
        sims_i2i = sims_i2i - neg_margin 
        sims_i2t = (sims_i2t - pos_margin) * ones.masked_fill_(torch.eq(ones, pos_mask), -1.0)
        sims = torch.cat([torch.clamp(sims_i2t, min=0.0) , torch.clamp(sims_i2i, min=0.0)], dim=-1) 
        loss =  torch.mean(torch.sum(sims.pow(2),dim=-1), dim=0) 
        return loss
        
        
    def itc_loss(self, image_embeds , text_embeds):
        bsize = text_embeds.shape[0]
        eye_mask = torch.eye(bsize).to(self.device) * 1e9
        sims_i2t = self.dist_func(image_embeds, text_embeds)
        sims_i2i = self.dist_func(image_embeds, image_embeds)

        target = torch.arange(bsize).to(self.device)
        contrastive_loss = 0.0 

        if self.config.manifold == EUCLID and self.config.euclid_pos_margin != 0.0:
            contrastive_loss = self.euclid_contrastive_loss(sims_i2t, sims_i2i) 
        elif self.config.manifold == LORENTZ and self.config.lorentz_neg_margin != 0.0:
            contrastive_loss = self.lorentz_contrastive_loss(sims_i2t, sims_i2i) 

        logits = torch.cat([sims_i2t/self.temp, sims_i2i/self.temp - eye_mask], dim=1)
        loss = F.cross_entropy(logits, target) + contrastive_loss 
        
        stats = {
            "logits/contrastive_loss": contrastive_loss.item(),
            "logits/min": sims_i2t.min().item(),
            "logits/mean": sims_i2t.mean().item(),
            "logits/max": sims_i2t.max().item(),
            "logits/acc": (sims_i2t.argmax(-1) == target).float().mean().item(),
        }
        return loss, stats, sims_i2t


    def forward(
        self,
        input_ids: Optional[torch.LongTensor] = None,
        pixel_values: Optional[torch.FloatTensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
    ) -> Union[Tuple, CLIPOutput]:

        vision_outputs = self.vision_model(
            pixel_values=pixel_values,
        )

        text_outputs = self.text_model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )

        image_embeds = vision_outputs[1]
        text_embeds = text_outputs[1]
        itc_loss, stats, sims_i2t = self.itc_loss(image_embeds, text_embeds)
        itm_loss = self.itm_loss(image_embeds, text_embeds, sims_i2t=sims_i2t)
        loss = itm_loss + itc_loss
        
        return loss, stats, itc_loss, itm_loss

    def get_text_features(
        self,
        input_ids: torch.Tensor, 
        attention_mask: torch.Tensor,
        position_ids: Optional[torch.Tensor] = None,
    ):
        text_outputs = self.text_model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
        )
        text_embeds = text_outputs[1]
        return text_embeds


    def get_vision_features(self, pixel_values:torch.Tensor):
        vision_outputs = self.vision_model(
            pixel_values=pixel_values,
        )
        return vision_outputs[1] 


        
    
if __name__ == '__main__':
    config = {
        'model_ckt': 'openai/clip-vit-base-patch32',
        'ft_out': 512,
        'manifold': 'poincare',
        'curvature': 0.1,
        'clip_radius': 2.3,
    }
    hypCLIP = HypCLIP(config)
    

    print(hypCLIP)


    