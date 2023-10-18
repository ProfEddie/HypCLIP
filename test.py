import torch
from transformers import (
    CLIPProcessor,
)
from datasets import load_dataset
from model.hypBLIP import LavisBLIP, LavisHypGraphBLIPWithQueue 
# from model.hypCLIP import HypGraphCLIPWithQueue 
from utils.data_utils import get_dataloader, lavis_preprocess_img
from trainer import MyTrainer
from trainer_lavis import MyTrainer as LavisTrainer
from accelerate import find_executable_batch_size
from utils.data_utils import get_flickr

from lavis.models import load_model_and_preprocess, load_model

if __name__ == "__main__":
    from config import parser
    from config import EUCLID, LORENTZ, POINCARE 

    config = parser.parse_args()
    model, vis_processors, txt_processors = load_model_and_preprocess("blip_retrieval", "flickr", is_eval=True)
    dataset = get_flickr(config.dataset, cache_dir=config.cache_dir)

    dataset = dataset.map(
        lambda sample: lavis_preprocess_img(sample, processor=vis_processors['eval'])
    ).remove_columns(["image"])
    dataset.set_format("numpy")


    @find_executable_batch_size(starting_batch_size=config.batch_size)
    def inner_training_loop(batch_size):
        config.batch_size = batch_size
        train_loader = get_dataloader(
            dataset["train"],
            config.batch_size,
            processor=model.tokenizer,
            mode="train",
            use_random_sampler=False,
        )
        test_loader = get_dataloader(
            dataset["test"], 5, processor=model.tokenizer, mode="test"
        )
        val_loader = get_dataloader(dataset["val"], 5, processor=model.tokenizer, mode="val")
        queue_model = LavisBLIP(config, model) if not config.use_graph else LavisHypGraphBLIPWithQueue(config, model)
        config.model_ckt = 'lavis/blip-base'

        trainer = LavisTrainer(
            model=queue_model,
            config=config,
            dataset=dataset,
            train_loader=train_loader,
            val_loader=val_loader,
            test_loader=test_loader,
            processor=model.tokenizer,
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
    config.hyp_margin_loss_weight=0.0
    for curv in [2.0]:
        config.curv = curv
        for use_graph in [False]:
            config.use_graph=use_graph
            for manifold in [LORENTZ, EUCLID]:
                config.manifold = manifold 
                inner_training_loop()
    