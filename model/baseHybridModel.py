import torch
import torch.nn as nn
from .modules.hyp_discriminator import LorentzDiscriminator as LorentzDisModel
from .modules.hyp_discriminator import HypDiscriminator 
from hyptorch.lorentz.manifold import CustomLorentz as Lorentz 
from hyptorch.geoopt.manifolds.lorentz import math as lmath 

from hyptorch.geoopt.manifolds.stereographic import PoincareBall 
# from model.manifolds.lorentz import Lorentz 
from typing import  Optional, Tuple, Union
from transformers.models.clip.modeling_clip import CLIPOutput
import torch.nn.functional as F


EUCLID = 'euclidean'
POINCARE = 'poincare'
LORENTZ = 'lorentz'
from .modules.utils import ManifoldMapper


class BaseModel(nn.Module):
    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.model_ckt = config.model_ckt
        self.ft_out = config.ft_out
        self.clip_r = config.clip_radius
        self.device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
        self.momentum = config.momentum
        self.queue_size = config.queue_size
        self.use_lorentz_centroid = config.use_lorentz_centroid
        self.logit_scale = nn.Parameter(torch.tensor(1 / config.temp).log())

        manifold = config.manifold
    
        assert manifold in [EUCLID, POINCARE, LORENTZ]

        self.weight_i2t = self.config.weight_i2t 
        self.curv = torch.as_tensor(config.curv if manifold != EUCLID else 0)
        if not torch.is_floating_point(self.curv):
            self.curv = self.curv.to(torch.get_default_dtype())
        
        self.visual_encoder_m = None
        self.text_encoder_m = None
        self.model_pairs = None 
    
        if manifold == POINCARE:
            self.curv = torch.nn.Parameter(self.curv, requires_grad=config.curv_learnable)
            self.manifold = PoincareBall(c=self.curv, learnable=config.curv_learnable)
            self.discriminator = HypDiscriminator(self.manifold, dim=(256 if 'blip' in self.config.model_ckt else 512), layer_dims=[512])
        else: 
            self.curv = torch.nn.Parameter(self.curv, requires_grad=config.curv_learnable)
            self.manifold = Lorentz(k=self.curv, learnable=config.curv_learnable)
            self.discriminator = LorentzDisModel(self.manifold, dim=(256 if 'blip' in self.config.model_ckt else 512), layer_dims=[256])

        self.mapper = ManifoldMapper(self.manifold, curv=self.curv, clip_r=self.clip_r, use_normalize=False)
        self.manifold_name =  manifold    
        self.vision_model = None 
        self.text_model = None 

    def num_parameters(self, only_trainable=True):
        num_params = 0
        if only_trainable:
            num_params = sum(p.numel() for p in self.parameters() if p.requires_grad)
        else:
            num_params = sum(p.numel() for p in self.parameters())
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
        
    def dist_func(self, x, y, device='gpu'):
        if self.manifold_name == POINCARE: 
            hyp_x = self.mapper(x)
            hyp_y = self.mapper(y)
            hyp_dis = -self.manifold.dist_batch(hyp_x, hyp_y, device=device)
        else: 
            hyp_x = self.mapper(x) 
            hyp_y = self.mapper(y) 
            hyp_dis = -self.manifold.dist_batch(hyp_x, hyp_y)

        eu_x = F.normalize(x,p=2, dim=-1) 
        eu_y = F.normalize(y,p=2, dim=-1) 
        euclid_dis = torch.matmul(eu_x, eu_y.t()) 
        return euclid_dis, hyp_dis


    def itm_loss(self, imgs, cap, sims_i2t):
        bs = imgs.shape[0]
        weights_i2t = F.softmax(sims_i2t, dim=1)
        weights_t2i = F.softmax(sims_i2t.T, dim=1)
        mask = (torch.eye(bs) > 0).to(self.device)

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


    def margin_loss(self, eu_sims_i2t, eu_sims_i2i, neg_margin):
        bsize = eu_sims_i2t.shape[0] 
        ones = torch.ones(bsize, bsize).to(self.device)
        pos_mask = torch.eye(bsize).to(self.device) 
        neg_mask = torch.ne(ones, pos_mask).float().to(self.device)
        sign = ones.masked_fill_(torch.eq(ones, pos_mask), -1.0) 

        neg_margin = neg_margin * neg_mask 
        pos_margin = self.config.euclid_pos_margin * pos_mask 
        eu_sims_i2t = eu_sims_i2t - neg_margin 
        eu_sims_i2i = eu_sims_i2i - neg_margin 
        eu_sims_i2t = (eu_sims_i2t - pos_margin) * sign 

    
        eu_sims = torch.cat([torch.clamp(eu_sims_i2t, min=0.0) , torch.clamp(eu_sims_i2i, min=0.0)], dim=-1) 
        loss =   torch.mean(torch.sum(eu_sims.pow(2),dim=-1), dim=0)
        return loss
        
    def itc_loss(self, image_embeds , text_embeds):
        bsize = text_embeds.shape[0]
        eye_mask = torch.eye(bsize).to(self.device) * 1e9
        self.logit_scale.data = torch.clamp(self.logit_scale.data, max=4.6052)
        _scale = self.logit_scale.exp()

        eu_sims_i2t, sims_i2t = self.dist_func(image_embeds, text_embeds) 
        eu_sims_t2i, sims_t2i = eu_sims_i2t.T, sims_i2t.T
        eu_sims_i2i, sims_i2i = self.dist_func(image_embeds, image_embeds) 
        eu_sims_t2t, sims_t2t = self.dist_func(text_embeds, text_embeds) 
        neg_text_margin = self.config.euclid_text_neg_margin
        neg_image_margin = self.config.euclid_img_neg_margin 
        target = torch.arange(bsize).to(self.device)
        logits_i2t = torch.cat([sims_i2t * _scale, sims_t2t * _scale - eye_mask], dim=1)
        logits_t2i = torch.cat([sims_t2i * _scale, sims_i2i* _scale - eye_mask], dim=1)

        margin_loss = self.margin_loss(eu_sims_i2t, eu_sims_t2t - eye_mask, neg_margin=neg_text_margin)  +  self.margin_loss(eu_sims_t2i, eu_sims_i2i - eye_mask, neg_margin=neg_image_margin)
        itc_loss = self.weight_i2t * F.cross_entropy(logits_i2t, target) + (1 - self.weight_i2t) * F.cross_entropy(logits_t2i, target) 

        loss = itc_loss + margin_loss 

        stats = {
            "logits/weight_t2i": 1.0 - self.weight_i2t,
            "logits/margin_loss": margin_loss.item() if margin_loss is not None else 0.0,
            # "logits/eu_itc_loss": eu_itc_loss.item(), 
            "logits/itc_loss": itc_loss.item(),
            "logits/min": sims_i2t.min().item(),
            "logits/mean": sims_i2t.mean().item(),
            "logits/max": sims_i2t.max().item(),
            "logits/acc": (sims_i2t.argmax(-1) == target).float().mean().item(),
            "logits/eu_acc": (eu_sims_i2t.argmax(-1) == target).float().mean().item(),
            "logits/curvature": self.manifold.k.item(),
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
        # self.manifold.assert_check_point_on_manifold(image_embeds)
        # self.manifold.assert_check_point_on_manifold(text_embeds)
        itc_loss, stats, sims_i2t = self.itc_loss(image_embeds, text_embeds)
        text_embeds = self.mapper(text_embeds)
        image_embeds = self.mapper(image_embeds)
        itm_loss = self.itm_loss(image_embeds, text_embeds, sims_i2t=sims_i2t)
        stats["logits/itm_loss"] = itm_loss.item() 
        loss =  itc_loss + itm_loss
        return loss, stats, itc_loss, 0 

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

