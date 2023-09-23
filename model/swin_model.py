import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoImageProcessor, AutoModel 
from torch_geometric.nn import GraphConv
from torch_geometric.nn import global_mean_pool
from torch.nn import AdaptiveAvgPool1d
from torch_geometric.data import Data, Batch

model_ckt = 'microsoft/swinv2-base-patch4-window12-192-22k'

processor = AutoImageProcessor.from_pretrained(model_ckt)

def build_visual_graphs(hidden_states ,proj_layers, factor=4):
  proj_outs = proj_layers(hidden_states)
  x = torch.cat(proj_outs, dim=1)
  bs = x.shape[0]
  starts = []
  ends = []
  cur_index = 0
  for i in range(len(proj_outs) - 1):
    next_index = cur_index + hidden_states[i].shape[1]
    if i < len(proj_outs) - 2:
      for j in range(hidden_states[i].shape[1]):
        current_start = cur_index + j
        starts.append(current_start)
        ends.append(next_index + (j)//factor)
    else:
      for j in range(hidden_states[i].shape[1]):
        current_start = cur_index + j
        starts.append(current_start)
        ends.append(next_index)

    cur_index += hidden_states[i].shape[1]

  edge_index = torch.tensor([starts, ends], dtype=torch.long)
  graphs = []
  for i in range(bs):
    graphs.append(Data(x=x[i,:,:], edge_index=edge_index))
  return Batch.from_data_list(graphs)


class GNN(torch.nn.Module):
    def __init__(self, ft_in ,hidden_channels, ft_out):
        super(GNN, self).__init__()
        torch.manual_seed(12345)
        self.conv1 = GraphConv(ft_in, hidden_channels)  # TODO
        self.conv2 = GraphConv(hidden_channels, hidden_channels)# TODO
        self.conv3 = GraphConv(hidden_channels, hidden_channels)  # TODO
        self.lin = nn.Linear(hidden_channels, ft_out)

    def forward(self, x, edge_index, batch):
        x = self.conv1(x, edge_index)
        x = x.relu()
        x = self.conv2(x, edge_index)
        x = x.relu()
        x = self.conv3(x, edge_index)
        x = global_mean_pool(x, batch)
        x = F.dropout(x, p=0.5, training=self.training)
        x = self.lin(x)
        
        return x

    

class ProjLayers(nn.Module):
    def __init__(self,  sizes=[128, 256,  512,  1024, 1024], proj_size=100, dropout=0.1, last_pooler:AdaptiveAvgPool1d=None):
        super().__init__()
        self.projectors = nn.ModuleList([nn.Linear(size, proj_size) for size in sizes])
        self.layer_norms = nn.ModuleList([nn.LayerNorm(proj_size) for size in sizes])
        self.dropout= nn.Dropout(dropout)
        if last_pooler is not None:
            self.last_pooler = last_pooler
        else:
            self.last_pooler = AdaptiveAvgPool1d(1)

    def forward(self, hidden_states):
        outputs = []
        for i in range(len(self.projectors)- 1):
            output= self.layer_norms[i](self.projectors[i](hidden_states[i]))
            outputs.append(output)
            pooled_output = self.last_pooler(hidden_states[-1].transpose(1, 2)).transpose(1, 2)
            outputs.append(self.layer_norms[i](self.projectors[i](pooled_output)))
        return outputs



class VisModel(nn.Module):
  def __init__(self, model_ckt, proj_dim, num_stages=3):
    super(VisModel, self).__init__()
    
    self.backbone = AutoModel.from_pretrained(model_ckt)
    self.init_stage = len(self.backbone.encoder.layers) - num_stages  
    self.factor = self.backbone.config.mlp_ratio 
    self.init_embed_size = self.backbone.config.embed_dim 
    self.backbone_hidden_sizes = [self.init_embed_size * 2**i for i in range(4)]
    self.backbone_hidden_sizes.append(self.init_embed_size * 8) 

    self.proj_layers = ProjLayers(sizes=self.backbone_hidden_sizes[self.init_stage:], proj_size=proj_dim, last_pooler=self.backbone.pooler)
    self.gnn = GNN(proj_dim, proj_dim, proj_dim)
    
  def num_parameters(self):
    return sum(p.numel() for p in self.gnn.parameters() if p.requires_grad)
    
  def forward(self, inputs):
    outputs = self.backbone(**inputs, output_hidden_states=True)
    graphs = build_visual_graphs(hidden_states=outputs.hidden_states[self.init_stage:], proj_layers=self.proj_layers)
    output = self.gnn(x=graphs.x, edge_index=graphs.edge_index, batch=graphs.batch)
    return output 



