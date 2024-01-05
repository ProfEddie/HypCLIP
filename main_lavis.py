from model.dctModel import DCTLAVISLIPWithQueue 
from lavis.datasets.builders import load_dataset
from trainer_queue import MyTrainer as LavisTrainer 
from trainer import MyTrainer as Blip2Trainer 
from utils.data_utils import  get_loaders
from lavis.models import load_model_and_preprocess

if __name__ == "__main__":
    from config import parser
    from config import POINCARE, EUCLID, LORENTZ, LAVIS_BLIP_BASE_FLICKR, LAVIS_BLIP_BASE_COCO, COCO, FLICKR
    COCO_PATH = "/mnt/data/itr_dataset/dataset/coco/images"
    FLICKR_PATH = "/mnt/data/itr_dataset/dataset/flickr30k/flickr30k_images"
    config = parser.parse_args()
    for dataset in [FLICKR, COCO]:
        config.dataset = dataset

        # tokenizer = model.tokenizer
        if "flickr" in config.dataset:
            config.model_ckt = LAVIS_BLIP_BASE_FLICKR
            model, vis_processors, txt_processors = load_model_and_preprocess("blip_retrieval", "flickr", is_eval=False)
            dataset = load_dataset("flickr30k", vis_path=FLICKR_PATH, cfg_path=None)
        else:
            model, vis_processors, txt_processors = load_model_and_preprocess("blip_retrieval", "coco", is_eval=False)
            config.model_ckt = LAVIS_BLIP_BASE_COCO 
            dataset = load_dataset("coco_retrieval", vis_path=COCO_PATH, cfg_path=None)


        def inner_training_loop(batch_size):
            config.batch_size = batch_size
            train_loader, val_loader, test_loader, test_img2txt, test_txt2img, _, _ = get_loaders(
                config.batch_size, 
                dataset,
                vis_processor=vis_processors['eval'],
                txt_processor=txt_processors['eval'],
                tokenizer=model.tokenizer,
                eval_batch_size=5
            )

            queue_model = DCTLAVISLIPWithQueue(config, model)
            trainer = LavisTrainer(
                model=queue_model,
                config=config,
                train_loader=train_loader,
                val_loader=val_loader,
                test_loader=test_loader,
                txt2img=test_txt2img,
                img2txt=test_img2txt
            )
            # print(trainer.evaluate(use_1k=False))
            # print(trainer.evaluate('val'))
            trainer.train()


        config.epochs = 3 
        config.enable_log = True
        config.use_margin_loss = False 

        for distil in [False]:
            config.distil = distil 
            # for compress_method in ['std','dct']:
            # for compress_method in ['std', 'dct', 'random', 'direct','none']:
            for compress_method in [
                'std-weighted-merge', 
                'bipartite-soft-matching',
                'std-mean-merge', 
                'dct', 
                'random-mean-merge',
            ]:
                config.compress_method = compress_method
                inner_training_loop(config.batch_size)
