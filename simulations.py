###############################################################################################
# dependancies
###############################################################################################
#%%
# Load deps
import os
import torch # type: ignore
import pickle
import random
import argparse
import numpy as np
import pandas as pd
import networkx as nx
from datetime import datetime
import matplotlib.pyplot as plt
from sklearn.manifold import TSNE
from sklearn.cluster import SpectralClustering
from sklearn.model_selection import train_test_split

import torch.nn as nn # type: ignore
from torch.nn.utils import clip_grad_norm_
import torch.nn.functional as F # type: ignore
from torch.utils.data import Dataset, DataLoader # type: ignore

###############################################################################################
# arguments
###############################################################################################
#%%
# Create the parser
parser = argparse.ArgumentParser(description="Trains a VAE on calcium data.")

# Add arguments
parser.add_argument(
    "-l",
    "--latent",
    type=int,
    required=False,
    default=32,
    help="Dimension of latent space (default: 32)"
)

parser.add_argument(
    "--hidden",
    type=int,
    required=False,
    default=256,
    help="Dimension of hidden layer (default: 256)"
)

parser.add_argument(
    "-e",
    "--epochs",
    type=int,
    required=False,
    default=50,
    help="Number of training epochs (default: 50)"
)

parser.add_argument(
    "-b",
    "--batch",
    type=int,
    required=False,
    default=16,
    help="Batch size (default: 16)"
)

parser.add_argument(
    "-r",
    "--rate",
    type=float,
    required=False,
    default=0.001,
    help="Learning rate (default: 0.001)"
)

parser.add_argument(
    "--beta_kl",
    type=float,
    required=False,
    default=1,
    help="KL divergence beta (default: 1)"
)

parser.add_argument(
    "--retrain",
    type=bool,
    required=False,
    default=False,
    help="Whether to retrain the model (default: False)"
)

# Parse the arguments
args = parser.parse_args()

# Access the arguments
beta = args.beta_kl         # beta=1
retrain = args.retrain      # retrain=True
batch_size = args.batch     # batch_size=256
latent_dim = args.latent    # latent_dim=32
hidden_dim = args.hidden    # hidden_dim=512
num_epochs = args.epochs    # num_epochs=200
learning_rate = args.rate   # learning_rate=0.002

###############################################################################################
# functions
###############################################################################################
#%%
def superprint(message):
    # Get the current date and time
    now = datetime.now()
    # Format the date and time
    timestamp = now.strftime("[%Y-%m-%d %H:%M:%S]")
    # Print the message with the timestamp
    print(f"{timestamp} {message}")

def read_roi(path,sample,group):

    # read in
    x = pd.read_csv(path)

    # assign sample and group
    x["sample"] = sample
    x["group"] = group

    return(x)

def read_trace(path,thin=False):

    # read in
    x = pd.read_csv(path,header=None,sep="\t")
    x = x.iloc[:, :30000]

    # thinning
    if thin:
        x = x.iloc[:, ::2]

    return(x)

# Create a custom PyTorch Dataset class
class MyDataset(Dataset):
    def __init__(self, data):
        self.data = data  # Assuming your data is already in the desired format

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        # Return a single data sample at the specified index
        sample = self.data[idx]
        return sample

class VAE(nn.Module):

    def __init__(self, input_dim, hidden_dim, latent_dim):
        super(VAE, self).__init__()

        # dimensions
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.latent_dim = latent_dim

        # Encoder layers
        self.encoder_lstm = nn.LSTM(input_dim, hidden_dim, batch_first=True)
        self.encoder_fc_mu = nn.Linear(hidden_dim, latent_dim)
        self.encoder_fc_logvar = nn.Linear(hidden_dim, latent_dim)

        # Decoder layers
        self.decoder_l1 = nn.Linear(latent_dim, hidden_dim)
        self.decoder_l2 = nn.LSTM(hidden_dim, input_dim, batch_first=True)

    def encode(self, x):

        # x.shape = (batch_size, input_dim)
        # x.unsqueeze(1).shape = (batch_size, 1, input_dim)
        # out.shape = (batch_size, 1, hidden_dim)
        # hd.shape = (1, batch_size, hidden_dim)
        # hd.squeeze(0).shape = (batch_size, hidden_dim)

        # get output of lstm
        _,(hd,_) = self.encoder_lstm(x.unsqueeze(1))

        # remove unnecessary dimension
        hd = hd.squeeze(0)

        # get parameters q(z|x)
        mu = self.encoder_fc_mu(hd)
        logvar = self.encoder_fc_logvar(hd)

        return mu, logvar

    def sample_z(self, mu, logvar):

        # reparametrize
        std = torch.exp(0.5 * logvar)

        # sample eps~(0,1)
        eps = torch.randn_like(std)
            # NOTE: randn_like means shape like std but still is a N(0,1)

        # return z
        return mu + eps * std

    def decode(self, z):

        # z.shape = (batch_size, latent_dim)
        # out_l1.shape = (batch_size, hidden_dim)
        # out_l1.unsqueeze(1).shape = (batch_size, 1, hidden_dim)
        # out_l2.shape = (batch_size, input_dim)

        # get linear layer output
        out = self.decoder_l1(z)

        # get lstm output (batch_size,1,)
        out, _ = self.decoder_l2(out.unsqueeze(1))

        return out.squeeze(1)

    def forward(self, x):

        # Encode
        mu, logvar = self.encode(x)

        # Reparameterize
        z = self.sample_z(mu, logvar)

        # Decode
        reconstructed_x = self.decode(z)

        return reconstructed_x, mu, logvar

# test vae
# vae = VAE(1000, 50, 10)
# x = torch.randn([32, 1000])
# mu, logvar = vae.encode(x)
# z = vae.sample_z(mu,logvar)
# xhat = vae.decode(z)

###############################################################################################
# IO
###############################################################################################
#%%
# dirs
bse_dir = "/media/HDD_4TB_1/jordi/calcium_imaging/simulations/set_2/"
rs_dir = f"{bse_dir}/Flat_RS/L_0.1/EI_ratio_0.8/Iz_param_noise_0.05/dt_0.001/fluo_noise_1.0/"
ch_dir = f"{bse_dir}/Flat_CH/L_0.1/EI_ratio_0.8/Iz_param_noise_0.05/dt_0.001/fluo_noise_1.0/"
ib_dir = f"{bse_dir}/Flat_IB/L_0.1/EI_ratio_0.8/Iz_param_noise_0.05/dt_0.001/fluo_noise_1.0/"
outdir = f"{bse_dir}/bVAE_2_lstm_lin/"

# make output dir
os.makedirs(outdir, exist_ok=True)

# output files
suff = f'latent_{latent_dim}_hidden_{hidden_dim}_epochs_{num_epochs}_rate_{learning_rate}_batch_{batch_size}_beta_{beta}'
mod_file = f'{outdir}/vae_{suff}.pkl'
curve_file = f'{outdir}/vae_{suff}.txt'
data_file = f'{outdir}/data_{suff}.npz'

# check if model file exists
if os.path.exists(mod_file) and not retrain:

    superprint("Model file already exists")
    exit(0)

###############################################################################################
# ROI
###############################################################################################

superprint("Loading data")

# ROI files
roi_chat_1_files = [f"{ch_dir}/Flat_CH_L_0.1_rois_{i}.txt" for i in range(1,6)]

# read in
roi_chat_1 = [read_roi(roi_chat_1_files[i],f"{i+1}","CH") for i in range(0,5)]

# merge dataframes
roi_chat_1 = pd.concat(roi_chat_1, axis=0)

# ROI files
roi_int_1_files = [f"{ib_dir}/Flat_IB_L_0.1_rois_{i}.txt" for i in range(1,6)]

# read in
roi_int_1 = [read_roi(roi_int_1_files[i],f"{i+1}","IB") for i in range(0,5)]

# merge dataframes
roi_int_1 = pd.concat(roi_int_1, axis=0)

# ROI files
roi_reg_1_files = [f"{rs_dir}/Flat_RS_L_0.1_rois_{i}.txt" for i in range(1,6)]

# read in
roi_reg_1 = [read_roi(roi_reg_1_files[i],f"{i+1}","RS") for i in range(0,5)]

# merge dataframes
roi_reg_1 = pd.concat(roi_reg_1, axis=0)

# concatenate
roi_df = pd.concat([roi_chat_1,roi_int_1,roi_reg_1])

###############################################################################################
# Traces
###############################################################################################

# files
trace_chat_1_files = [f"{ch_dir}/Flat_CH_L_0.1_calcium_{i}.txt.gz" for i in range(1,6)]

# read in
trace_chat_1 = [read_trace(trace_chat_1_files[i],thin=True) for i in range(0,5)]

# merge dataframes
trace_chat_1 = pd.concat(trace_chat_1, axis=0)

# files
trace_int_1_files = [f"{ib_dir}/Flat_IB_L_0.1_calcium_{i}.txt.gz" for i in range(1,6)]

# read in
trace_int_1 = [read_trace(trace_int_1_files[i],thin=True) for i in range(0,5)]

# merge dataframes
trace_int_1 = pd.concat(trace_int_1, axis=0)

# files
trace_reg_1_files = [f"{rs_dir}/Flat_RS_L_0.1_calcium_{i}.txt.gz" for i in range(1,6)]

# read in
trace_reg_1 = [read_trace(trace_reg_1_files[i],thin=True) for i in range(0,5)]

# merge dataframes
trace_reg_1 = pd.concat(trace_reg_1, axis=0)

# concatenate
trace_df = pd.concat([trace_chat_1,trace_int_1,trace_reg_1])

###############################################################################################
# prep data
###############################################################################################

superprint("Creating data loader")

# define numpy arrays
xdata = trace_df.to_numpy()
ydata = roi_df.to_numpy()

# work with float32
xdata = xdata.astype(np.float32)

# normalization
xdata = (xdata-xdata.min())/(xdata.max()-xdata.min()) - 0.5

# split the data into training and validation sets
xtrain, xval, ytrain, yval = train_test_split(xdata, ydata, test_size=0.20, random_state=42)

# make tensor
xval = torch.from_numpy(xval)

# store data
np.savez(data_file, xtrain=xtrain, xval=xval, ytrain=ytrain, yval=yval)

# create dataset object
dataset = MyDataset(xtrain)

# Create a DataLoader object
data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    # NOTE: check shape of tensor
    # >>> next(iter(data_loader)).shape
    # torch.Size([256, 15000])

###############################################################################################
# training
###############################################################################################

superprint("Setting up model")

# dimensions
input_dim = xtrain.shape[1]

# Check if GPU is available
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
if device.type=='cuda':
    superprint("Inference done on GPU")
else:
    superprint("Inference done on CPU")

# init model
superprint("Initializing VAE")
vae = VAE(input_dim, hidden_dim, latent_dim).to(device)

# send to gpu validation data as well
xval = xval.to(device)

# Define the optimizer
superprint("Setting up optimizer")
optimizer = torch.optim.Adam(vae.parameters(), lr=learning_rate, betas=(0.9, 0.999), eps=1e-6, weight_decay=0, amsgrad=False)

# Define the scheduler
scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.99)

# Define the maximum norm for gradient clipping
max_grad_norm = 1.0

# Early stopping parameters
counter = 0
patience = 5
min_delta = 0.005
best_val_loss = float('inf')

# store losses
val_loss = np.array([])
train_loss = np.array([])
train_kl_loss = np.array([])
train_rec_loss = np.array([])

superprint("Training starts now")

# Training loop
vae.train()  # Set the model in training mode
for epoch in range(num_epochs):

    # define aux variables
    running_loss = 0.0
    running_kl_loss = 0.0
    running_rec_loss = 0.0
    running_loss_val = 0.0

    # iterate over batches
    for batch_idx, inputs in enumerate(data_loader):

        # testing: inputs = next(iter(data_loader))
        # move input data to the GPU
        inputs = inputs.to(device)

        # zero the parameter gradients
        optimizer.zero_grad()

        # forward pass
        reconstructed_x, mu, logvar = vae(inputs)

        # average reconstruction error across samples in batch (equivalent to Gaussian model)
        rec_err = torch.sum(abs(reconstructed_x-inputs)**2) / batch_size
        running_rec_loss += rec_err.detach().cpu().numpy()

        # calculate KL divergence D(q(z|x)|p(z))
        kl_divergence = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp()) / batch_size
        running_kl_loss += kl_divergence.detach().cpu().numpy()

        # compute the training loss
        loss = rec_err + beta * kl_divergence

        # backward pass
        loss.backward()

        # apply gradient clipping
        clip_grad_norm_(vae.parameters(), max_grad_norm)

        # update the weights
        optimizer.step()

        # accumulate the loss
        running_loss += loss.item()

    ## validation loss after all batches are done

    # forward pass
    reconstructed_x, mu, logvar = vae(xval)

    # average reconstruction error across samples in batch (equivalent to Gaussian model)
    rec_err = torch.sum(abs(reconstructed_x-xval)**2) / reconstructed_x.shape[0]
    rec_err = rec_err.detach().cpu().numpy()

    # calculate KL divergence D(q(z|x)|p(z))
    kl_divergence = -0.5 * torch.sum(1 + logvar - mu.pow(2) - logvar.exp()) / reconstructed_x.shape[0]
    kl_divergence = kl_divergence.detach().cpu().numpy()

    # compute the loss - reconstruction loss has a much larger scale
    loss = rec_err + beta * kl_divergence

    # accumulate validation loss
    running_loss_val += loss.item()

    # Compute the average loss for the epoch
    mean_loss = running_loss / len(data_loader)
    train_loss = np.append(train_loss,mean_loss)

    # Compute the average reconstruction loss for the epoch
    mean_rec_loss = running_rec_loss / len(data_loader)
    train_rec_loss = np.append(train_rec_loss,mean_rec_loss)

    # Compute the average kl loss for the epoch
    mean_kl_loss = running_kl_loss / len(data_loader)
    train_kl_loss = np.append(train_kl_loss,mean_kl_loss)

    # Compute the average validation loss for the epoch
    mean_loss_val = running_loss_val / len(data_loader)
    val_loss = np.append(val_loss,mean_loss_val)

    # Step the scheduler at the end of the epoch
    scheduler.step()

    # Print the learning rate (optional)
    current_lr = optimizer.param_groups[0]['lr']
    str_print = f"Epoch: {epoch+1}/{num_epochs}, Lr: {mean_rec_loss:.2f}, Lkl: {mean_kl_loss:.2f}, Lt: {mean_loss:.2f}, lr: {current_lr:.4f}"
    superprint(str_print)

    # Check for early stopping
    if mean_loss_val < best_val_loss - min_delta:
        best_val_loss = mean_loss_val
        counter = 0
    else:
        counter += 1
        if counter >= patience:
            superprint(f'Early stopping triggered at epoch {epoch+1}')
            break

# Save the trained VAE to a file
with open(mod_file, 'wb') as file:
    pickle.dump(vae, file)

# store training curves
train_curves = pd.DataFrame({"train_loss":train_loss,"val_loss":val_loss,"train_rec_loss":train_rec_loss,"train_kl_loss":train_kl_loss})
train_curves.to_csv(curve_file,index=False)
