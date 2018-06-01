from __future__ import division

import numpy as np
from scipy import sparse
import matplotlib.pyplot as plt
import healpy as hp
from builtins import range


def healpix_weightmatrix(nside=16, nest=True, indexes=None, dtype=np.float32):
    '''Return an unnormalized weight matrix for a graph using the HEALPIX sampling.

    Parameters
    ----------
    nside : int
        The healpix nside parameter, must be a power of 2, less than 2**30.
    nest : bool, optional
        if True, assume NESTED pixel ordering, otherwise, RING pixel ordering
    indexes : list of int, optional
        List of indexes to use. This allows to build the graph from a part of
        the sphere only. If None, the default, the whole sphere is used.
    dtype : data-type, optional
        The desired data type of the weight matrix.
    '''

    if not nest:
        raise NotImplementedError()

    if indexes is None:
        indexes = range(nside**2 * 12)
    npix = len(indexes)  # Number of pixels.
    if npix >= (max(indexes)+1):
        # If the user input is not consecutive nodes, we need to use a slower
        # method.
        usefast = True
        indexes = range(npix)
    else:
        usefast = False
        indexes = list(indexes)

    # Get the coordinates.
    x, y, z = hp.pix2vec(nside, indexes, nest=nest)
    coords = np.vstack([x, y, z]).transpose()
    coords = np.asarray(coords, dtype=dtype)

    # Get the 8 neighbors.
    neighbors = hp.pixelfunc.get_all_neighbours(nside, indexes, nest=nest)
    # Indices of non-zero values in the adjacency matrix.
    col_index = neighbors.T.reshape((npix * 8))
    row_index = np.repeat(indexes, 8)

    # Remove pixels that are out of our indexes of interest (part of sphere).
    if usefast:
        keep = (col_index < npix)
        # Remove fake neighbors (some pixels have less than 8).
        keep &= (col_index >= 0)
        col_index = col_index[keep]
        row_index = row_index[keep]
    else:
        col_index_set = set(indexes)
        keep = [c in col_index_set for c in col_index]
        inverse_map = [np.nan]*(nside**2 *12)
        for i, index in enumerate(indexes):
            inverse_map[index] = i
        col_index = [inverse_map[el] for el in col_index[keep]]
        row_index = [inverse_map[el] for el in row_index[keep]]

    # Compute Euclidean distances between neighbors.
    distances = np.sum((coords[row_index] - coords[col_index])**2, axis=1)
    # slower: np.linalg.norm(coords[row_index] - coords[col_index], axis=1)**2

    # Compute similarities / edge weights.
    kernel_width = np.mean(distances)
    weights = np.exp(-distances / (2 * kernel_width))

    # Build the sparse matrix.
    W = sparse.csr_matrix((weights, (row_index, col_index)),
                          shape=(npix, npix), dtype=dtype)
    return W


def build_laplacian(W, lap_type='normalized', dtype=np.float32):
    d = np.ravel(W.sum(1))
    if lap_type == 'combinatorial':
        D = sparse.diags(d, 0, dtype=dtype)
        return (D - W).tocsc()
    elif lap_type == 'normalized':
        d12 = np.power(d, -0.5)
        D12 = sparse.diags(np.ravel(d12), 0, dtype=dtype).tocsc()
        return sparse.identity(d.shape[0], dtype=dtype) - D12 * W * D12
    else:
        raise ValueError('Unknown Laplacian type {}'.format(lap_type))


def healpix_graph(nside=16,
                  nest=True,
                  lap_type='normalized',
                  indexes=None,
                  dtype=np.float32):
    """Build a healpix graph using the pygsp from NSIDE."""
    from pygsp import graphs

    if indexes is None:
        indexes = range(nside**2 * 12)

    # 1) get the coordinates
    npix = hp.nside2npix(nside)  # number of pixels: 12 * nside**2
    pix = range(npix)
    x, y, z = hp.pix2vec(nside, pix, nest=nest)
    coords = np.vstack([x, y, z]).transpose()[indexes]
    # 2) computing the weight matrix
    W = healpix_weightmatrix(
        nside=nside, nest=nest, indexes=indexes, dtype=dtype)
    # 3) building the graph
    G = graphs.Graph(W, gtype='Healpix, Nside={}'.format(nside), lap_type=lap_type, coords=coords)
    return G


def healpix_laplacian(nside=16,
                      nest=True,
                      lap_type='normalized',
                      indexes=None,
                      dtype=np.float32):
    W = healpix_weightmatrix(
        nside=nside, nest=nest, indexes=indexes, dtype=dtype)
    L = build_laplacian(W, lap_type=lap_type)
    return L


def rescale_L(L, lmax=2):
    """Rescale the Laplacian eigenvalues in [-1,1]."""
    M, M = L.shape
    I = sparse.identity(M, format='csr', dtype=L.dtype)
    L /= lmax / 2
    L -= I
    return L


def build_laplacians(nsides, indexes=None):
    L = []
    p = []
    first = True
    if indexes is None:
        indexes = [None] * len(nsides)
    for nside, ind in zip(nsides, indexes):
        if not first:
            pval = (nside_old // nside)**2
            p.append(pval)
        nside_old = nside
        first = False
        Lt = healpix_laplacian(nside=nside, indexes=ind)
        L.append(Lt)
    if len(L):
        p.append(1)
    return L, p


def nside2indexes(nsides, order):
    """
    Return list of indexes from nside given a specific order

    This function return the necessary indexes for a scnn when
    only a part of the sphere is considered.

    Arguments
    ---------
    nsides : list of nside for the desired scale
    order  : parameter specifying the size of the sphere part
    """
    nsample = 12 * order**2
    indexes = [np.arange(hp.nside2npix(nside) // nsample) for nside in nsides]
    return indexes


def hp_split(img, order, nest=True):
    """
    Split the data of different part of the sphere.
    Return the splitted data and some possible index on the sphere.
    """
    npix = len(img)
    nside = hp.npix2nside(npix)
    if hp.nside2order(nside) < order:
        raise ValueError('Order not compatible with data.')
    if not nest:
        raise NotImplementedError('Implement the change of coordinate.')
    nsample = 12 * order**2
    return img.reshape([nsample, npix//nsample])


def histogram(x, cmin, cmax, bins=100):
    """
    Make histograms features vector from samples contained in a numpy array.
    """
    if x.ndim == 1:
        y, _ = np.histogram(x, bins=bins, range=[cmin, cmax])
        return y.astype(float)
    else:
        y = np.empty((len(x), bins), float)
        for i in range(len(x)):
            y[i], _ = np.histogram(x[i], bins=bins, range=[cmin, cmax])
        return y


def print_error(model, x, labels, name):
    """Compute and print the prediction error of a model."""
    pred = model.predict(x)
    error = sum(np.abs(pred - labels)) / len(labels)
    print('{} error: {:.2%}'.format(name, error))
    return error


def plot_filters_gnomonic(filters, order=10, ind=0, title='Filter {}->{}'):
    """Plot all filters in a filterbank in Gnomonic projection."""
    nside = hp.npix2nside(filters.G.N)
    reso = hp.pixelfunc.nside2resol(nside=nside, arcmin=True) * order / 70
    rot = hp.pix2ang(nside=nside, ipix=ind, nest=True, lonlat=True)

    maps = filters.localize(ind, order=order)

    nrows, ncols = filters.n_features_in, filters.n_features_out

    if maps.shape[0] == filters.G.N:
        # FIXME: old signal shape when not using Chebyshev filters.
        shape = (nrows, ncols, filters.G.N)
        maps = maps.T.reshape(shape)
    else:
        if nrows == 1:
            maps = np.expand_dims(maps, 0)
        if ncols == 1:
            maps = np.expand_dims(maps, 1)

    # Plot everything.
    # fig, axes = plt.subplots(nrows, ncols, figsize=(17, 17/ncols*nrows),
    #                          squeeze=False, sharex='col', sharey='row')

    # ymin, ymax = 1.05*maps.min(), 1.05*maps.max()
    for row in range(nrows):
        for col in range(ncols):
            map = maps[row, col, :]
            hp.gnomview(map.flatten(), nest=True, rot=rot, reso=reso, sub=(nrows, ncols, col+row*ncols+1),
                    title=title.format(row, col), notext=True)
            # if row == nrows - 1:
            #     #axes[row, col].xaxis.set_ticks_position('top')
            #     #axes[row, col].invert_yaxis()
            #     axes[row, col].set_xlabel('out map {}'.format(col))
            # if col == 0:
            #     axes[row, col].set_ylabel('in map {}'.format(row))
    # fig.suptitle('Gnomoinc view of the {} filters in the filterbank'.format(filters.n_filters))#, y=0.90)
    # return fig


def plot_filters_section(filters,
                         order=10,
                         xlabel='out map {}',
                         ylabel='in map {}',
                         title='Sections of the {} filters in the filterbank'):
    """Plot the sections of all filters in a filterbank."""

    nside = hp.npix2nside(filters.G.N)
    npix = hp.nside2npix(nside)

    # Create an inverse mapping from nest to ring.
    index = hp.reorder(range(npix), n2r=True)

    # Get the index of the equator.
    index_equator, ind = get_index_equator(nside, order)
    nrows, ncols = filters.n_features_in, filters.n_features_out

    maps = filters.localize(ind, order=order)
    if maps.shape[0] == filters.G.N:
        # FIXME: old signal shape when not using Chebyshev filters.
        shape = (nrows, ncols, filters.G.N)
        maps = maps.T.reshape(shape)
    else:
        if nrows == 1:
            maps = np.expand_dims(maps, 0)
        if ncols == 1:
            maps = np.expand_dims(maps, 1)

    # Make the x axis: angular position of the nodes in degree.
    angle = hp.pix2ang(nside, index_equator, nest=True)[1]
    angle -= abs(angle[-1] + angle[0]) / 2
    angle = angle / (2 * np.pi) * 360

    # Plot everything.
    fig, axes = plt.subplots(nrows, ncols, figsize=(17, 12/ncols*nrows),
                             squeeze=False, sharex='col', sharey='row')

    ymin, ymax = 1.05*maps.min(), 1.05*maps.max()
    for row in range(nrows):
        for col in range(ncols):
            map = maps[row, col, index_equator]
            axes[row, col].plot(angle, map,'o-')
            axes[row, col].set_ylim(ymin, ymax)
            if row == nrows - 1:
                #axes[row, col].xaxis.set_ticks_position('top')
                #axes[row, col].invert_yaxis()
                axes[row, col].set_xlabel(xlabel.format(col))
            if col == 0:
                axes[row, col].set_ylabel(ylabel.format(row))
    fig.suptitle(title.format(filters.n_filters))#, y=0.90)
    return fig


def plot_index_filters_section(filters, order=10, rot=(180,0,180)):
    """Plot the indexes used for the function `plot_filters_section`"""
    nside = hp.npix2nside(filters.G.N)
    npix = hp.nside2npix(nside)

    index_equator, center = get_index_equator(nside, order)

    sig = np.zeros([npix])
    sig[index_equator] = 1
    sig[center] = 2
    hp.mollview(sig, nest=True, title='', cbar=False, rot=rot)


def get_index_equator(nside, radius):
    """Return some indexes on the equator and the center of the index."""
    npix = hp.nside2npix(nside)

    # Create an inverse mapping from nest to ring.
    index = hp.reorder(range(npix), n2r=True)

    # Center index
    center = index[npix // 2]

    # Get the value on the equator back.
    equator_part = range(npix//2-radius, npix//2+radius+1)
    index_equator = index[equator_part]

    return index_equator, center


def psd(x):
    '''Spherical Power Spectral Densities'''
    if len(x.shape) == 2 and x.shape[1] > 1:
        return np.stack([psd(x[ind, ]) for ind in range(len(x))])
    hatx = hp.map2alm(hp.reorder(x, n2r=True))
    return hp.alm2cl(hatx)


def psd_unseen(x, Nside=1024):
    '''Spherical Power Spectral Densities for incomplete spherical data'''
    if len(x.shape) == 2 and x.shape[1] > 1:
        return np.stack([psd_unseen(x[ind, ]) for ind in range(len(x))])
    y = np.zeros(shape=[hp.nside2npix(Nside)])
    y[:] = hp.UNSEEN
    y[:len(x)] = x
    hatx = hp.map2alm(hp.reorder(y, n2r=True))
    return hp.alm2cl(hatx)
