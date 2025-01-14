import torch
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

import seaborn as sns
import sys

sys.path.append("../../..")
from data.st.load_st_data import load_st_data
from data.warps import apply_gp_warp
from models.gpsa_vi_lmc import VariationalWarpGP
from data.simulated.generate_oned_data import (
    generate_oned_data_affine_warp,
    generate_oned_data_gp_warp,
)
from plotting.callbacks import callback_oned, callback_twod

from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import WhiteKernel, RBF

## For PASTE
import scanpy as sc
import anndata
import matplotlib.patches as mpatches

sys.path.append("../../../paste")
from src.paste import PASTE, visualization

# import matplotlib

# LATEX_FONTSIZE = 30

# font = {"size": LATEX_FONTSIZE}
# matplotlib.rc("font", **font)
# matplotlib.rcParams["text.usetex"] = True


device = "cuda" if torch.cuda.is_available() else "cpu"

X_orig, Y_orig, view_idx = load_st_data(
    n_genes=20, log_transform=False, standardize=False
)
# n_samples_per_view = 100
n_samples_per_view = X_orig.shape[0]


# X = X[:, :1]
X, Y, n_samples_list, view_idx = apply_gp_warp(
    X_orig[:n_samples_per_view],
    Y_orig[:n_samples_per_view],
    n_views=2,
    kernel_variance=0.5,
    kernel_lengthscale=10,
    noise_variance=0.0,
)

for vv in range(len(view_idx)):
    X[view_idx[vv]] = X[view_idx[vv]] - X[view_idx[vv]].min(0)
    X[view_idx[vv]] = X[view_idx[vv]] / (X[view_idx[vv]] - X[view_idx[vv]].min(0)).max(
        0
    )
    X[view_idx[vv]] *= 10

# import ipdb; ipdb.set_trace()


n_spatial_dims = 2
n_views = 2
n_outputs = Y.shape[1]

m_G = 40
m_X_per_view = 40

N_EPOCHS = 2000
PRINT_EVERY = 200
N_LATENT_GPS = {"expression": 3}
NOISE_VARIANCE = 0.0

##  PASTE
slice1 = anndata.AnnData(Y[view_idx[0]])
slice2 = anndata.AnnData(Y[view_idx[1]])

slice1.obsm["spatial"] = X[view_idx[0]]
slice2.obsm["spatial"] = X[view_idx[1]]

pi12 = PASTE.pairwise_align(slice1, slice2, alpha=0.1)

slices = [slice1, slice2]
pis = [pi12]
# import ipdb; ipdb.set_trace()
new_slices = visualization.stack_slices_pairwise(slices, pis)


err_paste = np.mean(
    np.sum((new_slices[0].obsm["spatial"] - new_slices[1].obsm["spatial"]) ** 2, axis=1)
)
print("Error PASTE: {}".format(err_paste))

# slice_colors = ['#e41a1c','#377eb8','#4daf4a','#984ea3']
slice_colors = ["blue", "orange"]
markers = [".", "+"]


import matplotlib

font = {"size": 30}
matplotlib.rc("font", **font)
matplotlib.rcParams["text.usetex"] = True


plt.figure(figsize=(14, 7))
plt.subplot(121)
plt.scatter(
    slice1.obsm["spatial"][:, 0],
    slice1.obsm["spatial"][:, 1],
    s=400,
    c=np.log(slice1.X[:, 0] + 1),
    marker=markers[0],
)
plt.scatter(
    slice2.obsm["spatial"][:, 0],
    slice2.obsm["spatial"][:, 1],
    s=400,
    c=np.log(slice2.X[:, 0] + 1),
    marker=markers[1],
)
plt.axis("off")
plt.title("Observed data")

plt.subplot(122)
for i in range(len(new_slices)):
    plt.scatter(
        new_slices[i].obsm["spatial"][:, 0],
        new_slices[i].obsm["spatial"][:, 1],
        s=400,
        c=np.log(new_slices[i].X[:, 0] + 1),
        marker=markers[i],
        label="Sample {}".format(i + 1),
    )

plt.legend(fontsize=15)
plt.axis("off")
plt.title("Aligned data, PASTE")
plt.savefig("./out/paste_alignment.png")
# plt.show()
plt.close()

matplotlib.rcParams["text.usetex"] = False

Y = np.log(Y + 1)
## Standardize expression
Y = (Y - Y.mean(0)) / Y.std(0)


## Drop part of the second view (this is the part we'll try to predict)
x = torch.from_numpy(X).float().clone()
y = torch.from_numpy(Y).float().clone()

# import ipdb; ipdb.set_trace()

data_dict = {
    "expression": {
        "spatial_coords": x,
        "outputs": y,
        "n_samples_list": n_samples_list,
    }
}

model = VariationalWarpGP(
    data_dict,
    n_spatial_dims=n_spatial_dims,
    m_X_per_view=m_X_per_view,
    m_G=m_G,
    data_init=True,
    minmax_init=False,
    grid_init=False,
    n_latent_gps=N_LATENT_GPS,
    mean_function="identity_fixed",
    fixed_warp_kernel_variances=np.ones(n_views) * 0.1,
    fixed_warp_kernel_lengthscales=np.ones(n_views) * 10,
    # mean_function="identity_initialized",
    # fixed_view_idx=0,
).to(device)

view_idx, Ns, _, _ = model.create_view_idx_dict(data_dict)

optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)


def train(model, loss_fn, optimizer):
    model.train()

    # Forward pass
    G_means, G_samples, F_latent_samples, F_samples = model.forward(
        X_spatial={"expression": x}, view_idx=view_idx, Ns=Ns
    )

    # Compute loss
    loss = loss_fn(data_dict, F_samples)

    # Compute gradients and take optimizer step
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

    return loss.item(), G_means


# Set up figure.
fig = plt.figure(figsize=(14, 7), facecolor="white", constrained_layout=True)
ax_dict = fig.subplot_mosaic(
    [
        ["data", "latent"],
    ],
)
plt.show(block=False)

for t in range(N_EPOCHS):
    loss, G_means = train(model, model.loss_fn, optimizer)

    if t % PRINT_EVERY == 0:
        print("Iter: {0:<10} LL {1:1.3e}".format(t, -loss))
        # print(model.warp_kernel_variances.detach().numpy())

        # G_means_test, _, F_samples_test, _, = model.forward(
        #     X_spatial={"expression": x_test},
        #     view_idx=view_idx_test,
        #     Ns=Ns_test,
        #     prediction_mode=True,
        #     S=10,
        # )

        # curr_preds = torch.mean(F_samples_test["expression"], dim=0)

        callback_twod(
            model,
            X,
            Y,
            data_expression_ax=ax_dict["data"],
            latent_expression_ax=ax_dict["latent"],
            # prediction_ax=ax_dict["preds"],
            X_aligned=G_means,
            # X_test=X_test,
            # Y_test_true=Y_test,
            # Y_pred=curr_preds,
            # X_test_aligned=G_means_test,
        )
        plt.draw()
        plt.pause(1 / 60.0)

        err = np.mean(
            (
                G_means["expression"].detach().numpy().squeeze()[:n_samples_per_view]
                - G_means["expression"].detach().numpy().squeeze()[n_samples_per_view:]
            )
            ** 2
        )
        print("Error: {}".format(err))

plt.close()

import matplotlib

font = {"size": 30}
matplotlib.rc("font", **font)
matplotlib.rcParams["text.usetex"] = True

fig = plt.figure(figsize=(21, 7))
markers = [".", "+", "^"]
colors = ["blue", "orange"]

## Original data
plt.subplot(131)
plt.title("Data")
plt.scatter(
    slice1.obsm["spatial"][:, 0],
    slice1.obsm["spatial"][:, 1],
    s=400,
    c=np.log(slice1.X[:, 0] + 1),
    marker=markers[0],
)
plt.scatter(
    slice2.obsm["spatial"][:, 0],
    slice2.obsm["spatial"][:, 1],
    s=400,
    c=np.log(slice2.X[:, 0] + 1),
    marker=markers[1],
)
plt.axis("off")

## PASTE alignment
plt.subplot(132)
plt.title("Alignment, PASTE")
for i in range(len(new_slices)):
    plt.scatter(
        new_slices[i].obsm["spatial"][:, 0],
        new_slices[i].obsm["spatial"][:, 1],
        s=400,
        c=np.log(new_slices[i].X[:, 0] + 1),
        marker=markers[i],
        label="Sample {}".format(i + 1),
    )
plt.axis("off")

## GPSA alignment
plt.subplot(133)

model.eval()

plt.title("Alignment, GPSA")

curr_view_idx = model.view_idx["expression"]

for vv in range(model.n_views):

    plt.scatter(
        G_means["expression"].detach().numpy()[curr_view_idx[vv], 0],
        G_means["expression"].detach().numpy()[curr_view_idx[vv], 1],
        c=Y[curr_view_idx[vv], 0],
        label="View {}".format(vv + 1),
        marker=markers[vv],
        s=400,
    )
plt.axis("off")

plt.tight_layout()
plt.savefig("./out/two_d_simulation.png")
plt.show()
plt.close()
