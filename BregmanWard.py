import heapq
import numpy as np
import scipy.sparse

from sklearn.base import BaseEstimator

class BregmanWard(BaseEstimator):
    '''Agglomerative Bregman divergence clustering.
    More or less implements the general method described in

    Agglomerative Bregman Clustering. M. Telgarsky and S. Dasgupta, ICML 2012.

    This version also supports linkage constraints, which are useful for
    time-series segmentation.
    '''

    def __init__(self, n_clusters=2, connectivity=None, model='gaussian'):
        '''
        :parameters:
        - n_clusters : int
            Number of clusters to get at the end

        - connectivity : scipy.sparse.coo_matrix or None
            Connectivity graph ala sklearn.feature_extraction.image.grid_to_graph()
            If 'None', a fully connected (ie, unstructured) graph will be generated.

        - model : str, {'gaussian', 'diagonal-gaussian', 'multinomial'}
            Model family to use for clustering.

        :variables:
        - labels_ : array
            Cluster assignments after fitting the model
        '''
        self.n_clusters     = n_clusters
        self.connectivity   = connectivity

        if model == 'gaussian':
            self.model = Gaussian
        elif model == 'diagonal-gaussian':
            self.model = DiagonalGaussian
        elif model == 'multinomial':
            self.model = Multinomial
        else:
            raise ValueError('Invalid model type: ' + model)

    def build_models(self, X):

        S = self.model.get_smooth(X)

        return [self.model(xi, n=1, smoothing=S) for xi in X]

    def fit(self, X):
        '''
        :parameters:
            - X : ndarray, size=(n, d)
              The data to be clustered

        '''

        X = self.build_models(X)

        n = len(X)

        if self.connectivity is None:
            # No connectivity graph?
            # Fill in with a fully connected clique
            self.connectivity = scipy.sparse.coo_matrix(np.ones((n,n)))

        
        # Step 1: build the active vertices and edge map
        V = range(n)

        E = [set() for i in V]

        for (i, j) in np.vstack(self.connectivity.nonzero()).T:
            E[i].add(j)
            E[j].add(i)

        for i in V:
            E[i].discard(i)

        # ok, now we have a proper graph with iterable edge sets.

        # Initialize the set of active merge operations
        active          = set(V)

        # All frames initially contain themselves
        containers      = [set([x]) for x in V]

        # Children pointers
        children        = [ [i] for i in range(n) ]

        # The merger object
        mergers     = []
        sequence    = []

        def make_new_merge(i,j):
            # Construct the new candidate cluster node
            new_v       = X[i].merge(X[j])
            # Evaluate the cost of this cluster node
            cost        = X[i].n * X[i].distance(new_v) + X[j].n * X[j].distance(new_v)
            # Generate the new linkage constraints
            new_edge    = set() #(E[i] | E[j]) - (containers[i] | containers[j] | set([i,j]))
            # Find its constituents
            new_cont    = containers[i] | containers[j]
                
            # update the data structures
            X.append(new_v)
            V.append(len(X)-1)
            E.append(new_edge)

            children.append(set([i, j]))
            containers.append(new_cont)


            # add to the merger data structure
            heapq.heappush(mergers, (cost, i, j, len(X) - 1 ))

        # Initialize candidate mergers from the linkage graph
        for i in range(n):
            # Only take edges where j > i.
            # Since the graph is undirected, this eliminates redundant
            # merge objects.
            for j in filter(lambda x: x > i, E[i]):
                make_new_merge(i, j)


        childmap = {}
        for i in range(n):
            childmap[i] = i

        self.children_ = []

        while len(active) > 1:

            while True:
                (cost, s1, s2, sm) = heapq.heappop(mergers)
                if s1 in active and s2 in active:
                    break

            # The merged nodes are now inactive
            active.remove(s1)
            active.remove(s2)

            # The combined node is now active
            active.add(sm)
            
            sequence.append(sm)

            childmap[sm] = len(childmap)
            self.children_.append( [ childmap[s1], childmap[s2] ] )

            # Update the links, in case things have changed
            E[sm] = (E[s1] | E[s2]) - set([s1, s2])

            for i in E[sm]:
                # Replace the edges from active nodes
                E[i].discard(s1)
                E[i].discard(s2)
                E[i].add(sm)

                if i in active:
                    make_new_merge(i, sm)

        # Prune the tree to the desired number of components
        leaves = [sequence[-1]]
        while len(leaves) < min(n, self.n_clusters):
            node = leaves.pop(0)
            leaves.extend(list(children[node]))

        self.n_leaves_ = n

        self.labels_ = np.arange(n)
        for (label, leaf) in enumerate(leaves[::-1]):
            for x in containers[leaf]:
                self.labels_[x] = label

        self.children_ = np.array(self.children_)

        # Purge the intermediate nodes we constructed
        while len(X) > n:
            X.pop()

class _Callable:
    def __init__(self, fun):
        self.__call__ = fun


class Gaussian(object):
    '''A container class for gaussian models'''
    
    def __init__(self, mean, cov=0.0, n=1, smoothing=None):
        '''
        :parameters:
        - mean  : array (d,1)
          mean vector
        - cov : array (d,d)
          covariance matrix
        - n : int
          sample size
        - smoothing : array (d, d)
          smoothing values for bottom-level models. Gets added to covariance.
        '''
        self.mean    = mean
        self.cov     = cov 
        self.n       = n

        if smoothing is not None:
            self.cov = self.cov + smoothing
        
        self.dim     = len(mean)
        self.icov    = scipy.linalg.inv(self.cov)
        self.ldcov   = np.log(scipy.linalg.det(self.cov))
    
    def distance(self, other):
        ''' Compute the KL-divergence D(self || other)

        :parameters:
        - other : Gaussian
        '''
        
        mudiff = other.mean - self.mean
        
        D = np.sum(other.icov * self.cov)       \
          + mudiff.dot(other.icov.dot(mudiff))  \
          - self.dim                            \
          - self.ldcov + other.ldcov
        
        return D / 2.0
    
    
    def merge(self, other):
        ''' Merge this gaussian with another '''
        
        n = self.n + other.n
        
        mean = (self.n * self.mean + other.n * other.mean) / n
        
        cov = (self.n * (self.cov + np.outer(self.mean, self.mean)) \
            + other.n * (other.cov + np.outer(other.mean, other.mean))) / n
        
        cov = cov - np.outer(mean, mean)
        return Gaussian(mean, cov, n)

    def get_smooth(X, isotropic=False):
        
        n, d = X.shape

        scale = (4.0/(3.0 * n))**(0.2)
        if isotropic:
            sigma = np.std(X) * np.ones(d)
        else:
            sigma = np.std(X, axis=0)

        return np.diag(scale * sigma)
    # Make this into a static method
    get_smooth = _Callable(get_smooth)



class DiagonalGaussian(object):
    '''A container class for diagonal-covariance Gaussians'''

    def __init__(self, mean, var=0.0, n=1, smoothing=None):
        self.mean   = mean
        self.var    = var
        self.n      = n

        if smoothing is not None:
            self.var = self.var + smoothing

        self.dim    = len(mean)
        self.ivar   = self.var**-1.0
        self.ldvar  = np.sum(np.log(self.var))

    def distance(self, other):

        mudiff = other.mean - self.mean
        
        D = np.sum(other.ivar * self.var)       \
          + mudiff.dot(other.ivar * mudiff)  \
          - self.dim                            \
          - self.ldvar + other.ldvar

        return D / 2.0

    def merge(self, other):

        n = self.n + other.n
        
        mean = (self.n * self.mean + other.n * other.mean) / n
        
        var = (self.n * (self.var + self.mean**2 ) \
            + other.n * (other.var + other.mean**2)) / n
        
        var = var - mean**2

        return DiagonalGaussian(mean, var, n)

    def get_smooth(X, isotropic=False):
        
        n, d = X.shape

        scale = (4.0/(3.0 * n))**(0.2)
        if isotropic:
            sigma = np.std(X) * np.ones(d)
        else:
            sigma = np.std(X, axis=0)

        return scale * sigma
    # Make this into a static method
    get_smooth = _Callable(get_smooth)


class Multinomial(object):
    '''A container class for multinomial models'''

    def __init__(self, p, n=1, smoothing=None):
        '''
        :parameters:
        - p : array
          The distribution
        - n : int
          Sample size
        '''
    
        if smoothing is not None:
            p = p + smoothing

        # Just to ensure that we have a distribution
        p = np.maximum(p, 0.0)
        p = p / p.sum()

        self.p      = p
        self.logp   = np.log(p + (p==0))
        self.n      = n
        self.dim    = len(p)


    def distance(self, other):
        
        return self.p.dot(self.logp - other.logp)

    def merge(self, other):

        n = self.n + other.n
        p = (self.n * self.p + other.n * other.p) / n

        return Multinomial(p, n)

    def get_smooth(X):
        
        n, d = X.shape

        return 1.0/n + np.sqrt( (1.0/d) * (1.0 - 1.0/d) / n)

    # Make this into a static method
    get_smooth = _Callable(get_smooth)


