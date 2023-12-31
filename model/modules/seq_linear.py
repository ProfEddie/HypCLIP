import torch
import torch.nn.functional as F
import torch.nn as nn
from hyptorch.lorentz.blocks.layer_blocks import LFC_Block 
from hyptorch.geoopt import PoincareBall
from hyptorch.poincare.layers import MobiusLinear, MobiusAct 

import math
import torch
import torch.nn as nn
import torch.nn.init as init
import torch.nn.functional as F



def get_activate_func(act_func=None):
    if act_func is None or act_func.lower() == 'id':
        return nn.Identity()
    if act_func.lower() == 'relu':
        return nn.ReLU()
    if act_func.lower() == 'tanh':
        return nn.Tanh()
    if act_func.lower() == 'gelu':
        return nn.GELU()
    if act_func.lower() == 'elu':
        return nn.ELU()


class SeqLinear(nn.Module):
    def __init__(self, ft_in, layer_dims=[128], dropout=0.5, act_func='relu', bias=True):
        super(SeqLinear, self).__init__()
        self.linear = []
        self.norm = []
        self.dropout = []
        self.act = []
        for idx in range(len(layer_dims)):
            if idx == 0:
                self.linear.append(nn.Linear(ft_in, layer_dims[idx], bias=bias))
            else:
                self.linear.append(nn.Linear(layer_dims[idx-1], layer_dims[idx], bias=bias))
            if idx != len(layer_dims)-1:
                self.norm.append(nn.LayerNorm([layer_dims[idx]]))
                self.act.append(get_activate_func(act_func))
            self.dropout.append(nn.Dropout(p=dropout))
            
        self.linear = nn.ModuleList(self.linear)
        for x in self.linear:
            nn.init.kaiming_normal_(x.weight)
        self.norm = nn.ModuleList(self.norm)
        self.dropout = nn.ModuleList(self.dropout)
        self.act = nn.ModuleList(self.act)
        
    def forward(self, x):
        for idx in range(len(self.linear)):
            x = self.dropout[idx](x)
            x = self.linear[idx](x)
            if idx != (len(self.linear)-1): # last layer not use relu
                x = self.act[idx](x)
                x = self.norm[idx](x)
        return x  


class HypSeqLinear(nn.Module):
    def __init__(self, manifold ,ft_in, layer_dims, dropout=0.5, act_func='relu'):
        super(HypSeqLinear, self).__init__()
        self.manifold = manifold
        self.linear = []
        self.norm = []
        self.dropout = []
        self.act = []
        for idx in range(len(layer_dims)):
            if idx == 0:
                self.linear.append(MobiusLinear(ft_in, layer_dims[idx], manifold=manifold))
            else:
                self.linear.append(MobiusLinear(layer_dims[idx-1], layer_dims[idx], manifold=manifold))
            if idx != len(layer_dims)-1:
                self.norm.append(nn.LayerNorm([layer_dims[idx]]))
                self.act.append(MobiusAct(manifold=manifold, act=get_activate_func(act_func)))
            self.dropout.append(nn.Dropout(p=dropout))
            
        self.linear = nn.ModuleList(self.linear)
        self.norm = nn.ModuleList(self.norm)
        self.dropout = nn.ModuleList(self.dropout)
        self.act = nn.ModuleList(self.act)
        
    def forward(self, x):
        for idx in range(len(self.linear)):
            x = self.dropout[idx](x)
            x = self.linear[idx](x)
            if idx != (len(self.linear)-1): # last layer not use relu
                x = self.act[idx](x)
                x = self.manifold.logmap0(x)
                x = self.norm[idx](x)
                x = self.manifold.expmap0(x)
        return x  


class LorentzSeqLinear(nn.Module):
    def __init__(self, manifold ,ft_in, layer_dims, dropout=0.1, act_func='relu'):
        super(LorentzSeqLinear, self).__init__()
        self.linear = []
        self.norm = []
        self.dropout = []
        self.act = []
        for idx in range(len(layer_dims)):
            if idx == 0:
                self.linear.append(LFC_Block(manifold, ft_in, layer_dims[idx], dropout=dropout, activation=get_activate_func(act_func), LFC_normalize=False))
            elif idx < len(layer_dims) - 1:
                self.linear.append(LFC_Block(manifold, layer_dims[idx-1], layer_dims[idx], dropout=dropout, activation=get_activate_func(act_func), LFC_normalize=False))
            else:
                self.linear.append(LFC_Block(manifold, layer_dims[idx-1], layer_dims[idx], dropout=dropout)) 
            
        self.linear = nn.ModuleList(self.linear)
        
    def forward(self, x):
        for idx in range(len(self.linear)):
            x = self.linear[idx](x)
        return x  

        
class PoincareMLR(nn.Module):
    r"""
    Module which performs softmax classification
    in Hyperbolic space.
    """

    def __init__(self, manifold:PoincareBall, ball_dim, n_classes=2):
        super(PoincareMLR, self).__init__()
        self.a_vals = nn.Parameter(torch.Tensor(n_classes, ball_dim))
        self.p_vals = nn.Parameter(torch.Tensor(n_classes, ball_dim))
        self.n_classes = n_classes
        self.ball_dim = ball_dim
        self.manifold = manifold
        self.reset_parameters()

    def forward(self, x, c):
        p_vals_poincare = self.manifold.expmap0(self.p_vals)

        conformal_factor = 1 - c * p_vals_poincare.pow(2).sum(dim=1, keepdim=True)
        a_vals_poincare = self.a_vals * conformal_factor
        logits = self.manifold.hyperbolic_softmax(x, a_vals_poincare, p_vals_poincare, c)
        return logits

    def extra_repr(self):
        return "Poincare ball dim={}, n_classes={}, c={}".format(
            self.ball_dim, self.n_classes
        )

    def reset_parameters(self):
        init.kaiming_uniform_(self.a_vals, a=math.sqrt(5))
        init.kaiming_uniform_(self.p_vals, a=math.sqrt(5))

