import torch
from transformers import (
    CLIPProcessor,
)
from datasets import load_dataset
from model.hypBLIP import LavisBLIP, LavisHypGraphBLIPWithQueue 
from model.hypCLIP import HypCLIPDistilled 
from utils.data_utils import get_dataloader, lavis_preprocess_img
from trainer import MyTrainer
from coTrainer import MyTrainer as Trainer 
from accelerate import find_executable_batch_size
from utils.data_utils import get_flickr, get_co_dataloader, co_preprocess_img, CoFlickr_dataset
from transformers import CLIPProcessor 

from lavis.models import load_model_and_preprocess, load_model

if __name__ == "__main__":
    from config import parser
    from config import EUCLID, LORENTZ, POINCARE 

    config = parser.parse_args()
    blip_model, vis_processors, txt_processors = load_model_and_preprocess("blip_retrieval", "flickr", is_eval=True)
    dataset = get_flickr(config.dataset, cache_dir=config.cache_dir)
    clip_processor = CLIPProcessor.from_pretrained(
        config.model_ckt, cache_dir=config.cache_dir
    )

    dataset = dataset.map(
        lambda sample: co_preprocess_img(sample, blip_processor=vis_processors['eval'], clip_processor=clip_processor)
    ).remove_columns(["image"])
    dataset.set_format("numpy")


    @find_executable_batch_size(starting_batch_size=config.batch_size)
    def inner_training_loop(batch_size):
        config.batch_size = batch_size
        train_loader = get_co_dataloader(
            dataset["train"],
            config.batch_size,
            blip_processor=blip_model.tokenizer,
            clip_processor=clip_processor,
            mode="train",
            use_random_sampler=False,
        )
        test_loader = get_co_dataloader(
            dataset["test"], 
            5, 
            blip_processor=blip_model.tokenizer, 
            clip_processor=clip_processor ,
            mode="test"
        )
        val_loader = get_co_dataloader(
            dataset["val"], 
            5, 
            blip_processor=blip_model.tokenizer, 
            clip_processor=clip_processor ,
            mode="val"
        )
        model = HypCLIPDistilled(config, blip_model)

        trainer = Trainer(
            model=model,
            config=config,
            dataset=dataset,
            train_loader=train_loader,
            val_loader=val_loader,
            test_loader=test_loader,
            blip_processor=blip_model.tokenizer,
            clip_processor=clip_processor,
        )
        # metric = trainer.evaluate(mode='test')
        # print(metric)
        # metric = trainer.evaluate(mode='val')
        # print(metric)
        trainer.train()
    # print(model)
    # inner_training_loop()

    config.epochs = 5 
    config.enable_log = True 
    config.eval_freq = 725 
    config.hyp_margin_loss_weight=1.0
    for curv in [2.0]:
        config.curv = curv
        for use_graph in [False]:
            config.use_graph=use_graph
            for manifold in [LORENTZ, EUCLID]:
                config.manifold = manifold 
                inner_training_loop()
    