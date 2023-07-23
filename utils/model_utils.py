from transformers import CLIPVisionModel, CLIPTextModel


def freeze_clip(vision_model:CLIPVisionModel,text_model:CLIPTextModel=None ,num_trainable_blocks=1):
    def fr(m):
        for param in m.parameters():
            param.requires_grad = False

    fr(vision_model.vision_model.embeddings)
    for idx in range(len(vision_model.vision_model.encoder.layers)-num_trainable_blocks):
        fr(vision_model.vision_model.encoder.layers[idx])

    if text_model is not None:
        fr(text_model.text_model.embeddings)
        for idx in range(len(text_model.text_model.encoder.layers)-num_trainable_blocks):
            fr(text_model.text_model.encoder.layers[idx])
