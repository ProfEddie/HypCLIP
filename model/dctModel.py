import torch
import torch.nn as nn
from transformers import AutoModel 
from .modules.compressed_models import CompressedLAVISBLIP, CompressedHFCLIP, CompressedHFBLIP, CompressedLAVISBLIP2
from peft import get_peft_model, LoraConfig, TaskType
from typing import  Optional, Tuple, Union
from .modules.utils import freeze_blip
from model.baseQueueModel import BaseModelWithQueue 
from model.baseModel import BaseModel 
from model.baseBlip2Model import BaseModel  as Blip2Model
from lavis.models import load_model_and_preprocess
import math

EUCLID = "euclidean"
POINCARE = "poincare"
LORENTZ = "lorentz"
def get_lora_clip(config, model):
    target_modules = [
        'visual_projection',
        'text_projection'
    ]
    max_len = 11 if 'base' in config.model_ckt else 23
  
    for i in range(config.text_trainable_blocks): 
        index = max_len - i
        target_modules.extend([
            f'text_model.encoder.layers.{index}.self_attn.out_proj',
            f'text_model.encoder.layers.{index}.self_attn.q_proj',
            f'text_model.encoder.layers.{index}.self_attn.k_proj',
            f'text_model.encoder.layers.{index}.self_attn.v_proj', 
            f'text_model.encoder.layers.{index}.mlp.fc1', 
            f'text_model.encoder.layers.{index}.mlp.fc2', 
        ])
    for i in range(config.vision_trainable_blocks): 
        index = max_len - i
        target_modules.extend([
            f'vision_model.encoder.layers.{index}.self_attn.out_proj',
            f'vision_model.encoder.layers.{index}.self_attn.q_proj',
            f'vision_model.encoder.layers.{index}.self_attn.k_proj',
            f'vision_model.encoder.layers.{index}.self_attn.v_proj', 
            f'vision_model.encoder.layers.{index}.mlp.fc1', 
            f'vision_model.encoder.layers.{index}.mlp.fc2', 
        ])
    peft_config = LoraConfig(
        task_type=TaskType.FEATURE_EXTRACTION, 
        inference_mode=False, 
        r=32, 
        lora_alpha=32, 
        lora_dropout=0.2, 
        target_modules=target_modules
    )

    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()
    return model 




def get_lora_blip(config, model):

    target_modules = [ 
        'text_proj', 
        'vision_proj',
    ]
    for i in range(config.vision_trainable_blocks): 
        index = 11 - i
        target_modules.extend([
            f'vision_model.encoder.layers.{index}.self_attn.qkv',
            f'vision_model.encoder.layers.{index}.self_attn.projection',
            f'vision_model.encoder.layers.{index}.mlp.fc1', 
            f'vision_model.encoder.layers.{index}.mlp.fc2', 
        ])
    for i in range(config.text_trainable_blocks): 
        index = 11 - i
        target_modules.extend([
            f'text_model.encoder.layer.{index}.attention.output.dense', 
            f'text_model.encoder.layer.{index}.attention.self.query', 
            f'text_model.encoder.layer.{index}.attention.self.value',
            f'text_model.encoder.layer.{index}.attention.self.key', 
        ])
    peft_config = LoraConfig(
        task_type=TaskType.FEATURE_EXTRACTION, 
        inference_mode=False, 
        r=32, 
        lora_alpha=32, 
        lora_dropout=0.2, 
        target_modules=target_modules
    )
    model = get_peft_model(model, peft_config) 
    model.print_trainable_parameters()
    return model




def get_lora_lavis_blip(config, model):

    target_modules = [ 
        'text_proj', 
        'vision_proj',
    ]
    for i in range(config.vision_trainable_blocks): 
        index = 11 - i
        target_modules.extend([
            f'visual_encoder.blocks.{index}.attn.qkv',
            f'visual_encoder.blocks.{index}.attn.proj',
            f'visual_encoder.blocks.{index}.mlp.fc1', 
            f'visual_encoder.blocks.{index}.mlp.fc2', 
        ])
    for i in range(config.text_trainable_blocks): 
        index = 11 - i
        target_modules.extend([
            f'text_encoder.encoder.layer.{index}.attention.output.dense', 
            f'text_encoder.encoder.layer.{index}.attention.self.query', 
            f'text_encoder.encoder.layer.{index}.attention.self.value',
            f'text_encoder.encoder.layer.{index}.attention.self.key', 
        ])
    peft_config = LoraConfig(
        task_type=TaskType.FEATURE_EXTRACTION, 
        inference_mode=False, 
        r=32, 
        lora_alpha=32, 
        lora_dropout=0.2, 
        target_modules=target_modules
    )
    model = get_peft_model(model, peft_config) 
    model.print_trainable_parameters()
    return model

    
def get_lora_blip2(config, model):

    target_modules = [ 
        'text_proj', 
        'vision_proj',
    ]
    for i in range(config.vision_trainable_blocks): 
        index = 47 - i
        target_modules.extend([
            f'visual_encoder.blocks.{index}.attn.qkv',
            f'visual_encoder.blocks.{index}.attn.proj',
            f'visual_encoder.blocks.{index}.mlp.fc1', 
            f'visual_encoder.blocks.{index}.mlp.fc2', 
        ])
    for i in range(config.text_trainable_blocks): 
        index = 11 - i
        target_modules.extend([
            f'Qformer.bert.encoder.layer.{index}.attention.output.dense', 
            f'Qformer.bert.encoder.layer.{index}.attention.self.query', 
            f'Qformer.bert.encoder.layer.{index}.attention.self.value',
            f'Qformer.bert.encoder.layer.{index}.attention.self.key', 
        ])
    peft_config = LoraConfig(
        task_type=TaskType.FEATURE_EXTRACTION, 
        inference_mode=False, 
        r=32, 
        lora_alpha=32, 
        lora_dropout=0.2, 
        target_modules=target_modules
    )
    model = get_peft_model(model, peft_config) 
    model.print_trainable_parameters()
    return model

class DCTHFWithQueue(BaseModelWithQueue):
    def __init__(self, config) -> None:
        super(DCTHFWithQueue, self).__init__(config)
        model = AutoModel.from_pretrained(config.model_ckt, cache_dir=config.cache_dir)
        if 'clip' in config.model_ckt:
            model = get_lora_clip(config, model=model) 
            self.model = CompressedHFCLIP(model, compress_method=config.compress_method, r=config.r)
        else:
            model = get_lora_blip(config, model=model) 
            self.model = CompressedHFBLIP(model, compress_method=config.compress_method, r=config.r)

        
        self._init_queue(config, model.config.projection_dim)
    
    def get_vision_features(self, pixel_values: torch.Tensor, use_compressed_hidden_state=True):
        image_output = self.model.get_vision_features(pixel_values=pixel_values, use_compressed_hidden_state=use_compressed_hidden_state)
        image_feat = self.postprocess_embeds(image_output[1])
        return image_feat, image_output[0],  image_output[4]
    
class DCTLAVISLIPWithQueue(BaseModelWithQueue):
    def __init__(self, config, model) -> None:
        super(DCTLAVISLIPWithQueue, self).__init__(config)
        model = get_lora_lavis_blip(config, model=model) 
        self.model = CompressedLAVISBLIP(model, compress_method=config.compress_method, r=config.r)
        
        self._init_queue(config, 256)
    
    def get_vision_features(self, pixel_values: torch.Tensor, use_compressed_hidden_state=True):
        image_output = self.model.get_vision_features(pixel_values=pixel_values, use_compressed_hidden_state=use_compressed_hidden_state)
        image_feat = self.postprocess_embeds(image_output[1])
        return image_feat, image_output[0], image_output[4]

class CompressedLAVISBLIP2WithQueue(Blip2Model):
    def __init__(self, config, model) -> None:
        super(CompressedLAVISBLIP2WithQueue, self).__init__(config)
        model = get_lora_blip2(config, model=model) 
        self.model = CompressedLAVISBLIP2(model, compress_method=config.compress_method, r=config.r)
        
    
    def get_vision_features(self, pixel_values: torch.Tensor, use_compressed_hidden_state=True):
        image_output = self.model.get_vision_features(pixel_values=pixel_values, use_compressed_hidden_state=use_compressed_hidden_state)
        image_feat = self.postprocess_embeds(image_output[1])
        return image_feat, image_output[0], image_output[4]
