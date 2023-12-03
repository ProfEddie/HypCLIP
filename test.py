from lavis.datasets.builders import load_dataset
from utils.data_utils import get_fused_dataloader, get_fused_loaders
from model.modules.utils import prepare_processors_and_models
from model.baseFuseModel import BaseModelWithQueue as FuseModel
from trainer_perceiver import MyTrainer as FuseTrainer
from config import parser
from config import (
    EUCLID,
    LORENTZ,
    POINCARE,
    CLIP_LARGE_PATCH_14, 
    CLIP_BASE_PATCH_16, 
    LAVIS_BLIP_BASE_FLICKR, 
    LAVIS_BLIP_BASE_COCO, 
    COCO_PATH, 
    FLICKR_PATH
)

config = parser.parse_args()
def run(config, vis_processors, txt_processors, tokenizers, dataset, models):
    train_loader, val_loader, test_loader = get_fused_loaders(
        dataset,
        vis_processors=vis_processors,
        txt_processors=txt_processors,
        tokenizers=tokenizers,
    )

    queue_model = FuseModel(config, models) 
    trainer = FuseTrainer(
        model=queue_model,
        config=config,
        train_loader=train_loader,
        val_loader=val_loader,
        test_loader=test_loader,
    )
    # print(trainer.evaluate('test'))
    # print(trainer.evaluate('val'))
    trainer.train()


if __name__ == '__main__':
    dataset = load_dataset("flickr30k", vis_path=FLICKR_PATH, cfg_path=None)
    model_ckts = [
        LAVIS_BLIP_BASE_COCO, 
        CLIP_BASE_PATCH_16, 
    ]

    if "flickr" in config.dataset:
        config.model_ckt = LAVIS_BLIP_BASE_FLICKR
        dataset = load_dataset("flickr30k", vis_path=FLICKR_PATH, cfg_path=None)
    else:
        config.model_ckt = LAVIS_BLIP_BASE_COCO 
        dataset = load_dataset("coco_retrieval", vis_path=COCO_PATH, cfg_path=None)


    tokenizers, vis_processors, txt_processors, models = prepare_processors_and_models(model_ckts)
    config.epochs = 100 
    config.enable_log = True 
    config.use_margin_loss = False 
    for manifold in [EUCLID]:
        config.manifold = manifold 
        run(config, vis_processors=vis_processors, tokenizers=tokenizers, txt_processors=txt_processors, dataset=dataset, models=models)
            
