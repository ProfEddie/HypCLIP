
import torch
import torch.nn as nn
from .utils import freeze_clip, freeze_blip 
from typing import Optional
from .seq_linear import LorentzSeqLinear 
from .graphs import GraphHead

class CLIPVision(nn.Module): 
    def __init__(self,config, body, head, num_trainable_blocks=0, freeze_embedding=True) -> None:
        super().__init__()

        self.body = body
        self.head = head 
        self.config = config


    def forward(
            self,
            pixel_values: Optional[torch.FloatTensor] = None,
    ) -> torch.FloatTensor:

        vision_outputs = self.body(
            pixel_values=pixel_values,
            return_dict=True,
        )

        last_hidden_state = vision_outputs[0]

        if not self.config.use_lorentz_centroid or self.config.manifold != 'lorentz':
            pooled_output = vision_outputs[1]
        else:
            pooled_output = last_hidden_state
        for layer in self.head:
            pooled_output = layer(pooled_output)

        return last_hidden_state, pooled_output
    
class CLIPGraphVision(nn.Module): 
    def __init__(self, config ,body, head, manifold_mapper=None, num_layers=1) -> None:
        super().__init__()
        self.body = body
        self.head = head 
        self.manifold_mapper = manifold_mapper
        self.graph_head = GraphHead(sizes=[768] * num_layers, proj_hidden_sizes=[512, 512], ft_out=512) 
        self.dropout = nn.Dropout(0.5)
        self.final_proj = nn.Linear(1024, 512)
        self.config = config
        
    def forward(
        self,
        input_ids: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.Tensor] = None,
    ) -> torch.FloatTensor:

        text_outputs = self.body(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
            return_dict=True,
        )

        pooled_output = text_outputs[1]
        last_hidden_state = text_outputs[0]

        pooled_output = self.head(pooled_output)
        output = torch.cat([self.graph_head(text_outputs.hidden_states, pooled_output), pooled_output], dim=-1)
        output = self.final_proj(self.dropout(output))
        if self.manifold_maper is not None:
            output = self.manifold_mapper(output)

        return last_hidden_state, output


class BLIPVision(nn.Module): 
    def __init__(self, config, body, head ) -> None:
        super().__init__()

        self.body = body
        self.head = head 
        self.config = config

    def forward(
            self,
            pixel_values: Optional[torch.FloatTensor] = None,
    ) -> torch.FloatTensor:

        vision_outputs = self.body(
            pixel_values=pixel_values,
            return_dict=False,
        )

        last_hidden_state = vision_outputs[0]

        if not self.config.use_lorentz_centroid or self.config.manifold != 'lorentz':
            pooled_output = last_hidden_state[:, 0, :]
        else:
            pooled_output = last_hidden_state
        for layer in self.head:
            pooled_output = layer(pooled_output)

        return last_hidden_state, pooled_output
    
     


