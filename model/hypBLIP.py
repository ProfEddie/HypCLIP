import torch
import torch.nn as nn
from .modules.text_model import BLIPText 
from .modules.vision_model import BLIPVision 
from .modules.discriminator import Discriminator as DisModel
from .modules.seq_linear import  LorentzSeqLinear, HypSeqLinear
from .modules.hyp_discriminator import HypDiscriminator as HypDisModel
from .modules.hyp_discriminator import LorentzDiscriminator as LorentzDisModel
from .manifolds.euclidean import Euclidean 
from .manifolds.hyperboloid import Hyperboloid 
from .manifolds.lorentz import Lorentz 
from .manifolds.poincare import PoincareBall 
from transformers import BlipVisionModel, BlipTextModel, BlipForImageTextRetrieval
from typing import  Optional, Tuple, Union
from transformers.models.clip.modeling_clip import CLIPOutput
import torch.nn.functional as F
from .modules.utils import ManifoldMapper 
from model.baseModel import BaseModel

EUCLID = 'euclidean'
POINCARE = 'poincare'
LORENTZ = 'lorentz'


class HypBLIP(BaseModel):
    def __init__(self, config) -> None:
        super(HypBLIP, self).__init__(config)
     
        model = BlipForImageTextRetrieval.from_pretrained(self.model_ckt, cache_dir=config.cache_dir) 
        text_body = model.text_encoder
        vision_body = model.vision_model
        text_head = nn.ModuleList([model.text_proj ,ManifoldMapper(self.manifold, curv=self.curv, clip_r=self.clip_r)])
        vision_head = nn.ModuleList([model.vision_proj, ManifoldMapper(self.manifold, curv=self.curv, clip_r=self.clip_r)])

        if self.manifold_name == EUCLID:
            text_head.append(nn.Linear(text_body.config.hidden_size, self.ft_out, bias=False))
            vision_head.append(nn.Linear(vision_body.config.hidden_size, self.ft_out, bias=False))
        elif self.manifold_name == LORENTZ: 
            text_head.append(LorentzSeqLinear(manifold=self.manifold, ft_in=text_body.config.hidden_size, layer_dims=[self.ft_out]))
            vision_head.append(LorentzSeqLinear(manifold=self.manifold, ft_in=vision_body.config.hidden_size, layer_dims=[self.ft_out]))
        else: 
            text_head.append(HypSeqLinear(manifold=self.manifold, c=self.curv ,ft_in=text_body.config.hidden_size, layer_dims=[self.ft_out]))
            vision_head.append(HypSeqLinear(manifold=self.manifold, c=self.curv ,ft_in=vision_body.config.hidden_size, layer_dims=[self.ft_out]))
        self.vision_model = BLIPVision(body=vision_body, head=vision_head, num_trainable_blocks=config.vision_trainable_blocks, freeze_embedding=config.freeze_embedding)
        self.text_model = BLIPText(body=text_body, head=text_head, num_trainable_blocks=config.text_trainable_blocks, freeze_embeddings=config.freeze_embedding)

