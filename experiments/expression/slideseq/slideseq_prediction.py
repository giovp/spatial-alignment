import torch
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

import seaborn as sns
import sys
from os.path import join as pjoin
import scanpy as sc
import anndata
from sklearn.metrics import r2_score, mean_squared_error

from gpsa import VariationalGPSA, rbf_kernel
from gpsa.plotting import callback_twod

from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import WhiteKernel, RBF, Matern

from scipy.sparse import load_npz

## For PASTE
import scanpy as sc
import anndata
import matplotlib.patches as mpatches

from sklearn.neighbors import NearestNeighbors, KNeighborsRegressor
from sklearn.metrics import r2_score

device = "cuda" if torch.cuda.is_available() else "cpu"


def scale_spatial_coords(X, max_val=10.0):
    X = X - X.min(0)
    X = X / X.max(0)
    return X * max_val


DATA_DIR = "../../../data/slideseq/mouse_hippocampus"
N_GENES = 10
N_SAMPLES = 2000

n_spatial_dims = 2
n_views = 2
m_G = 200
m_X_per_view = 200

N_LATENT_GPS = {"expression": None}

N_EPOCHS = 2000
PRINT_EVERY = 100

FRAC_TEST = 0.2
N_REPEATS = 10

GENE_IDX_TO_TEST = np.arange(N_GENES)


def process_data(adata, n_top_genes=2000):
    adata.var_names_make_unique()
    adata.var["mt"] = adata.var_names.str.startswith("MT-")
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], inplace=True)

    sc.pp.filter_cells(adata, min_counts=500)  # 1800
    # sc.pp.filter_cells(adata, max_counts=35000)
    # adata = adata[adata.obs["pct_counts_mt"] < 20]
    # sc.pp.filter_genes(adata, min_cells=10)

    sc.pp.normalize_total(adata, inplace=True)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(
        adata, flavor="seurat", n_top_genes=n_top_genes, subset=True
    )
    return adata


spatial_locs_slice1 = pd.read_csv(
    pjoin(DATA_DIR, "Puck_200115_08_spatial_locs.csv"), index_col=0
)
expression_slice1 = load_npz(pjoin(DATA_DIR, "Puck_200115_08_expression.npz"))
gene_names_slice1 = pd.read_csv(
    pjoin(DATA_DIR, "Puck_200115_08_gene_names.csv"), index_col=0
)
barcode_names_slice1 = pd.read_csv(
    pjoin(DATA_DIR, "Puck_200115_08_barcode_names.csv"), index_col=0
)

data_slice1 = anndata.AnnData(
    X=expression_slice1, obs=barcode_names_slice1, var=gene_names_slice1
)
data_slice1.obsm["spatial"] = spatial_locs_slice1.values
data_slice1 = process_data(data_slice1, n_top_genes=6000)


spatial_locs_slice2 = pd.read_csv(
    pjoin(DATA_DIR, "Puck_191204_01_spatial_locs.csv"), index_col=0
)
expression_slice2 = load_npz(pjoin(DATA_DIR, "Puck_191204_01_expression.npz"))
gene_names_slice2 = pd.read_csv(
    pjoin(DATA_DIR, "Puck_191204_01_gene_names.csv"), index_col=0
)
barcode_names_slice2 = pd.read_csv(
    pjoin(DATA_DIR, "Puck_191204_01_barcode_names.csv"), index_col=0
)

data_slice2 = anndata.AnnData(
    X=expression_slice2, obs=barcode_names_slice2, var=gene_names_slice2
)
data_slice2.obsm["spatial"] = spatial_locs_slice2.values
data_slice2 = process_data(data_slice2, n_top_genes=6000)


if N_SAMPLES is not None:
    rand_idx = np.random.choice(
        np.arange(data_slice1.shape[0]), size=N_SAMPLES, replace=False
    )
    data_slice1 = data_slice1[rand_idx]
    rand_idx = np.random.choice(
        np.arange(data_slice2.shape[0]), size=N_SAMPLES, replace=False
    )
    data_slice2 = data_slice2[rand_idx]

    # rand_idx = np.random.choice(
    #     np.arange(data.shape[0]), size=N_SAMPLES * 2, replace=False
    # )
    # data = data[rand_idx]


## Remove outlier points outside of puck
MAX_NEIGHBOR_DIST = 700
knn = NearestNeighbors(n_neighbors=10).fit(data_slice1.obsm["spatial"])
neighbor_dists, _ = knn.kneighbors(data_slice1.obsm["spatial"])
inlier_idx = np.where(neighbor_dists[:, -1] < MAX_NEIGHBOR_DIST)[0]
data_slice1 = data_slice1[inlier_idx]

knn = NearestNeighbors(n_neighbors=10).fit(data_slice2.obsm["spatial"])
neighbor_dists, _ = knn.kneighbors(data_slice2.obsm["spatial"])
inlier_idx = np.where(neighbor_dists[:, -1] < MAX_NEIGHBOR_DIST)[0]
data_slice2 = data_slice2[inlier_idx]


## Save original data
plt.figure(figsize=(10, 5))
plt.subplot(121)
plt.scatter(
    data_slice1.obsm["spatial"][:, 0],
    data_slice1.obsm["spatial"][:, 1],
    # c=np.log(np.array(data_slice1.X[:, 0].todense()) + 1)
    s=3,
)
plt.title("Slice 1", fontsize=30)
plt.gca().invert_yaxis()
plt.axis("off")

plt.subplot(122)
plt.scatter(
    data_slice2.obsm["spatial"][:, 0],
    data_slice2.obsm["spatial"][:, 1],
    # c=np.log(np.array(data_slice2.X[:, 0].todense()) + 1)
    s=3,
)
plt.title("Slice 2", fontsize=30)
plt.gca().invert_yaxis()
plt.axis("off")
plt.savefig("./out/slideseq_original_slices.png")
# plt.show()
plt.close()
# import ipdb

# ipdb.set_trace()


angle = 1.45
slice1_coords = data_slice1.obsm["spatial"].copy()
slice2_coords = data_slice2.obsm["spatial"].copy()
slice1_coords = scale_spatial_coords(slice1_coords, max_val=10) - 5
slice2_coords = scale_spatial_coords(slice2_coords, max_val=10) - 5

R = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
slice2_coords = slice2_coords @ R

slice2_coords += np.array([1.0, 1.0])

data_slice1.obsm["spatial"] = slice1_coords
data_slice2.obsm["spatial"] = slice2_coords

print(data_slice1.shape, data_slice2.shape)

data = data_slice1.concatenate(data_slice2)

## Remove genes with no variance
shared_gene_names = data.var.gene_ids.index.values
data_slice1 = data_slice1[:, shared_gene_names]
data_slice2 = data_slice2[:, shared_gene_names]

nonzerovar_idx = np.intersect1d(
    np.where(np.array(data_slice1.X.todense()).var(0) > 0)[0],
    np.where(np.array(data_slice2.X.todense()).var(0) > 0)[0],
)

data = data[:, nonzerovar_idx]
data_slice1 = data_slice1[:, nonzerovar_idx]
data_slice2 = data_slice2[:, nonzerovar_idx]

# import ipdb

# ipdb.set_trace()

data_knn = data_slice1 #[:, shared_gene_names]
X_knn = data_knn.obsm["spatial"]
Y_knn = np.array(data_knn.X.todense())
Y_knn = (Y_knn - Y_knn.mean(0)) / Y_knn.std(0)
# nbrs = NearestNeighbors(n_neighbors=2).fit(X_knn)
# distances, indices = nbrs.kneighbors(X_knn)

knn = KNeighborsRegressor(n_neighbors=10, weights="uniform").fit(X_knn, Y_knn)
preds = knn.predict(X_knn)

# preds = Y_knn[indices[:, 1]]
r2_vals = r2_score(Y_knn, preds, multioutput="raw_values")

gene_idx_to_keep = np.where(r2_vals > 0.3)[0]
N_GENES = min(N_GENES, len(gene_idx_to_keep))
gene_names_to_keep = data_knn.var.gene_ids.index.values[gene_idx_to_keep]
gene_names_to_keep = gene_names_to_keep[np.argsort(-r2_vals[gene_idx_to_keep])]
r2_vals_sorted = -1 * np.sort(-r2_vals[gene_idx_to_keep])
if N_GENES < len(gene_names_to_keep):
    gene_names_to_keep = gene_names_to_keep[:N_GENES]
data = data[:, gene_names_to_keep]


# if N_SAMPLES is not None:
#     rand_idx = np.random.choice(
#         np.arange(data_slice1.shape[0]), size=N_SAMPLES, replace=False
#     )
#     data_slice1 = data_slice1[rand_idx]
#     rand_idx = np.random.choice(
#         np.arange(data_slice2.shape[0]), size=N_SAMPLES, replace=False
#     )
#     data_slice2 = data_slice2[rand_idx]

#     # rand_idx = np.random.choice(
#     #     np.arange(data.shape[0]), size=N_SAMPLES * 2, replace=False
#     # )
#     # data = data[rand_idx]
# data = data_slice1.concatenate(data_slice2)

all_slices = anndata.concat([data_slice1, data_slice2])
n_samples_list = [data[data.obs.batch == str(ii)].shape[0] for ii in range(n_views)]

X1 = np.array(data[data.obs.batch == "0"].obsm["spatial"])
X2 = np.array(data[data.obs.batch == "1"].obsm["spatial"])
Y1 = np.array(data[data.obs.batch == "0"].X.todense())
Y2 = np.array(data[data.obs.batch == "1"].X.todense())


Y1 = (Y1 - Y1.mean(0)) / Y1.std(0)
Y2 = (Y2 - Y2.mean(0)) / Y2.std(0)

X = np.concatenate([X1, X2])
Y = np.concatenate([Y1, Y2])

view_idx = [
    np.arange(X1.shape[0]),
    np.arange(X1.shape[0], X1.shape[0] + X2.shape[0]),
]

errors_union, errors_separate, errors_gpsa = [], [], []

for repeat_idx in range(N_REPEATS):

    ## Drop part of the second view (this is the part we'll try to predict)
    second_view_idx = view_idx[1]
    n_drop = int(1.0 * n_samples_list[1] * FRAC_TEST)
    test_idx = np.random.choice(second_view_idx, size=n_drop, replace=False)

    ## Only test on interior of tissue
    interior_idx = np.where(
        (X[:, 0] > 2.5) & (X[:, 0] < 7.5) & (X[:, 1] > 2.5) & (X[:, 1] < 7.5)
    )[0]
    test_idx = np.intersect1d(interior_idx, test_idx)
    n_drop = test_idx.shape[0]

    keep_idx = np.setdiff1d(second_view_idx, test_idx)

    train_idx = np.concatenate([np.arange(n_samples_list[0]), keep_idx])

    X_train = X[train_idx]
    Y_train = Y[train_idx]
    n_samples_list_train = n_samples_list.copy()
    n_samples_list_train[1] -= n_drop

    n_samples_list_test = [[0], [n_drop]]

    X_test = X[test_idx]
    Y_test = Y[test_idx]

    gene_idx_to_keep = np.logical_and(np.var(Y_train, axis=0) > 1e-1, np.var(Y_test, axis=0) > 1e-1)
    GENE_IDX_TO_TEST = np.intersect1d(GENE_IDX_TO_TEST, gene_idx_to_keep)
    Y_train = Y_train[:, gene_idx_to_keep]
    Y_test = Y_test[:, gene_idx_to_keep]

    # import ipdb; ipdb.set_trace()

    x_train = torch.from_numpy(X_train).float().clone()
    y_train = torch.from_numpy(Y_train).float().clone()
    x_test = torch.from_numpy(X_test).float().clone()
    y_test = torch.from_numpy(Y_test).float().clone()

    data_dict_train = {
        "expression": {
            "spatial_coords": x_train,
            "outputs": y_train,
            "n_samples_list": n_samples_list_train,
        }
    }

    data_dict_test = {
        "expression": {
            "spatial_coords": x_test,
            "outputs": y_test,
            "n_samples_list": n_samples_list_test,
        }
    }
    

    model = VariationalGPSA(
        data_dict_train,
        n_spatial_dims=n_spatial_dims,
        m_X_per_view=m_X_per_view,
        m_G=m_G,
        data_init=True,
        minmax_init=False,
        grid_init=False,
        n_latent_gps=N_LATENT_GPS,
        mean_function="identity_fixed",
        kernel_func_warp=rbf_kernel,
        kernel_func_data=rbf_kernel,
        # fixed_warp_kernel_variances=np.ones(n_views) * 1.,
        # fixed_warp_kernel_lengthscales=np.ones(n_views) * 10,
        fixed_view_idx=0,
    ).to(device)

    view_idx_train, Ns_train, _, _ = model.create_view_idx_dict(data_dict_train)
    view_idx_test, Ns_test, _, _ = model.create_view_idx_dict(data_dict_test)

    ## Make predictions for naive alignment
    gpr_union = GaussianProcessRegressor(kernel=RBF() + WhiteKernel())
    gpr_union.fit(X=X_train, y=Y_train)
    preds = gpr_union.predict(X_test)

    knn = KNeighborsRegressor(n_neighbors=10)
    knn.fit(X=X_train, y=Y_train)
    preds = knn.predict(X_test)
    
    # error_union = np.mean(np.sum((preds - Y_test) ** 2, axis=1))
    error_union = r2_score(Y_test[:, GENE_IDX_TO_TEST], preds[:, GENE_IDX_TO_TEST]) #, multioutput="raw_values")

    errors_union.append(error_union)
    print("MSE, union: {}".format(round(error_union, 5)), flush=True)
    #
    # print("R2, union: {}".format(round(r2_union, 5)))
    # import ipdb; ipdb.set_trace()

    ## Make predictons for each view separately
    preds, truth = [], []

    for vv in range(n_views):
        
        curr_trainX = X_train[view_idx_train["expression"][vv]]
        curr_trainY = Y_train[view_idx_train["expression"][vv]]
        curr_testX = X_test[view_idx_test["expression"][vv]]
        curr_testY = Y_test[view_idx_test["expression"][vv]]
        if len(curr_testX) == 0:
            continue

        # gpr_separate = GaussianProcessRegressor(kernel=RBF() + WhiteKernel())
        # gpr_separate.fit(X=curr_trainX, y=curr_trainY)
        # curr_preds = gpr_separate.predict(curr_testX)

        knn = KNeighborsRegressor(n_neighbors=10)
        knn.fit(X=curr_trainX, y=curr_trainY)
        curr_preds = knn.predict(curr_testX)

        preds.append(curr_preds)
        truth.append(curr_testY)

    preds = np.concatenate(preds, axis=0)
    truth = np.concatenate(truth, axis=0)
    # error_separate = np.mean(np.sum((preds - truth) ** 2, axis=1))
    error_separate = r2_score(truth[:, GENE_IDX_TO_TEST], preds[:, GENE_IDX_TO_TEST])

    print("MSE, separate: {}".format(round(error_separate, 5)), flush=True)

    # print("R2, sep: {}".format(round(r2_sep, 5)))

    errors_separate.append(error_separate)

    optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)

    def train(model, loss_fn, optimizer):
        model.train()

        # Forward pass
        G_means, G_samples, F_latent_samples, F_samples = model.forward(
            X_spatial={"expression": x_train}, view_idx=view_idx_train, Ns=Ns_train, S=3
        )

        # Compute loss
        loss = loss_fn(data_dict_train, F_samples)

        # Compute gradients and take optimizer step
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        return loss.item(), G_means

    # Set up figure.
    fig = plt.figure(figsize=(18, 7), facecolor="white", constrained_layout=True)
    data_expression_ax = fig.add_subplot(131, frameon=False)
    latent_expression_ax = fig.add_subplot(132, frameon=False)
    prediction_ax = fig.add_subplot(133, frameon=False)

    plt.show(block=False)

    for t in range(N_EPOCHS):
        loss, G_means = train(model, model.loss_fn, optimizer)

        if t % PRINT_EVERY == 0 or t == N_EPOCHS - 1:
            print("Iter: {0:<10} LL {1:1.3e}".format(t, -loss))

            G_means_test, _, _, F_samples_test, = model.forward(
                X_spatial={"expression": x_test},
                view_idx=view_idx_test,
                Ns=Ns_test,
                prediction_mode=True,
                S=10,
            )

            curr_preds = torch.mean(F_samples_test["expression"], dim=0)

            # callback_twod(
            #     model,
            #     X_train,
            #     Y_train,
            #     data_expression_ax=data_expression_ax,
            #     latent_expression_ax=latent_expression_ax,
            #     # prediction_ax=ax_dict["preds"],
            #     X_aligned=G_means,
            #     # X_test=X_test,
            #     # Y_test_true=Y_test,
            #     # Y_pred=curr_preds,
            #     # X_test_aligned=G_means_test,
            # )
            # plt.draw()
            # plt.pause(1 / 60.0)

            error_gpsa = np.mean(
                np.sum((Y_test - curr_preds.detach().numpy()) ** 2, axis=1)
            )
            # print("MSE, GPSA: {}".format(round(error_gpsa, 5)), flush=True)
            # r2_gpsa = r2_score(Y_test, curr_preds.detach().numpy())
            # print("R2, GPSA: {}".format(round(r2_gpsa, 5)))

            curr_aligned_coords = G_means["expression"].detach().numpy()
            curr_aligned_coords_test = G_means_test["expression"].detach().numpy()

            try:
                # gpr_gpsa = GaussianProcessRegressor(kernel=RBF() + WhiteKernel())
                # gpr_gpsa.fit(X=curr_aligned_coords, y=Y_train)
                # preds = gpr_gpsa.predict(curr_aligned_coords_test)
                knn = KNeighborsRegressor(n_neighbors=10)
                knn.fit(X=curr_aligned_coords, y=Y_train)
                preds = knn.predict(curr_aligned_coords_test)
                # error_gpsa = np.mean(np.sum((preds - Y_test) ** 2, axis=1))
                error_gpsa = r2_score(Y_test[:, GENE_IDX_TO_TEST], preds[:, GENE_IDX_TO_TEST])
                print("MSE, GPSA GPR: {}".format(round(error_gpsa, 5)), flush=True)
            except:
                continue

    errors_gpsa.append(error_gpsa)

    plt.close()

    results_df = pd.DataFrame(
        {
            "Union": errors_union[: repeat_idx + 1],
            "Separate": errors_separate[: repeat_idx + 1],
            "GPSA": errors_gpsa[: repeat_idx + 1],
        }
    )
    results_df_melted = pd.melt(results_df)
    results_df_melted.to_csv("./out/twod_prediction_slideseq.csv")

    plt.figure(figsize=(7, 5))
    sns.boxplot(data=results_df_melted, x="variable", y="value", color="gray")
    plt.xlabel("")
    plt.ylabel("MSE")
    plt.tight_layout()
    plt.savefig("./out/two_d_prediction_slideseq.png")
    # plt.show()
    plt.close()

    # import ipdb; ipdb.set_trace()
