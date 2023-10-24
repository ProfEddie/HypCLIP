import torch
import torch.nn.functional as F
import numpy as np
from torchmetrics.functional import retrieval_recall
from torcheval.metrics.functional.ranking import retrieval_precision



def evaluate_recall(sims_t2i:torch.Tensor, mode='val'):

    sims_t2i = sims_t2i.numpy()
    recall_t2i = t2i(sims_t2i)
    recall_i2t = i2t(sims_t2i.T)
    
    r1i, r5i, r10i = recall_i2t
    r1t, r5t, r10t = recall_t2i
    output = {
        f'{mode}/r1_i2t': r1i,
        f'{mode}/r5_i2t': r5i,
        f'{mode}/r10_i2t': r10i,
        f'{mode}/r_i2t': r1i + r5i + r10i,
        f'{mode}/r1_t2i': r1t,
        f'{mode}/r5_t2i': r5t,
        f'{mode}/r10_t2i': r10t,
        f'{mode}/r_t2i': r1t + r5t + r10t,
        f'{mode}/r_all': r1t + r5t + r10t + r1i + r5i + r10i,
    }
    return output 

def i2t(sims_i2t):
    # sims (n_imgs, n_caps)
    n_imgs, _ = sims_i2t.shape
    ranks = np.zeros(n_imgs)
    for index in range(n_imgs):
        inds = np.argsort(sims_i2t[index])[::-1]
        rank = 1e20
        for i in range(5 * index, 5 * index + 5, 1):
            tmp = np.where(inds == i)[0][0]
            if tmp < rank:
                rank = tmp
        ranks[index] = rank


    # Compute metrics
    r1 = 1.0 * len(np.where(ranks < 1)[0]) / len(ranks)
    r5 = 1.0 * len(np.where(ranks < 5)[0]) / len(ranks)
    r10 = 1.0 * len(np.where(ranks < 10)[0]) / len(ranks)
    return r1, r5, r10

def t2i(sims_t2i):
    # sims (n_caps, n_imgs)
    _, n_imgs = sims_t2i.shape
    ranks = np.zeros(5*n_imgs)
    # --> (5N(caption), N(image))
    results = []
    for index in range(n_imgs):
        for i in range(5):
            inds = np.argsort(sims_t2i[5 * index + i])[::-1]
            # print(index)
            ranks[5 * index + i] = np.where(inds == index)[0][0]
            # print(np.where(inds == index))


    # Compute metrics
    r1 = 1.0 * len(np.where(ranks < 1)[0]) / len(ranks)
    r5 = 1.0 * len(np.where(ranks < 5)[0]) / len(ranks)
    r10 = 1.0 * len(np.where(ranks < 10)[0]) / len(ranks)
    return r1, r5, r10