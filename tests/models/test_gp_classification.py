#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates.
# All rights reserved.

# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

import unittest

import numpy as np
import numpy.testing as npt
import torch
from aepsych.acquisition import MCLevelSetEstimation
from aepsych.config import Config
from aepsych.generators import OptimizeAcqfGenerator, SobolGenerator
from aepsych.models import GPClassificationModel
from aepsych.strategy import SequentialStrategy, Strategy
from botorch.acquisition import qUpperConfidenceBound
from botorch.acquisition.objective import GenericMCObjective
from scipy.stats import bernoulli, norm, pearsonr
from sklearn.datasets import make_classification
from torch.distributions import Normal

from ..common import cdf_new_novel_det, f_1d, f_2d


class GPClassificationSmoketest(unittest.TestCase):
    """
    Super basic smoke test to make sure we know if we broke the underlying model
    for single-probit  ("1AFC") model
    """

    def setUp(self):
        np.random.seed(1)
        torch.manual_seed(1)
        X, y = make_classification(
            n_samples=100,
            n_features=1,
            n_redundant=0,
            n_informative=1,
            random_state=1,
            n_clusters_per_class=1,
        )
        self.X, self.y = torch.Tensor(X), torch.Tensor(y)

    def test_1d_classification(self):
        """
        Just see if we memorize the training set
        """
        X, y = self.X, self.y
        model = GPClassificationModel(
            torch.Tensor([-3]), torch.Tensor([3]), inducing_size=10
        )

        model.fit(X[:50], y[:50])

        # pspace
        pm, _ = model.predict(X[:50], probability_space=True)
        pred = (pm > 0.5).numpy()
        npt.assert_allclose(pred, y[:50])

        # fspace
        pm, _ = model.predict(X[:50], probability_space=False)
        pred = (pm > 0).numpy()
        npt.assert_allclose(pred, y[:50])

        # smoke test update
        model.update(X, y)

        # pspace
        pm, _ = model.predict(X, probability_space=True)
        pred = (pm > 0.5).numpy()
        npt.assert_allclose(pred, y)

        # fspace
        pm, _ = model.predict(X, probability_space=False)
        pred = (pm > 0).numpy()
        npt.assert_allclose(pred, y)

    def test_1d_classification_different_scales(self):
        """
        Just see if we memorize the training set
        """
        np.random.seed(1)
        torch.manual_seed(1)
        X, y = make_classification(
            n_features=2,
            n_redundant=0,
            n_informative=1,
            random_state=1,
            n_clusters_per_class=1,
        )
        X, y = torch.Tensor(X), torch.Tensor(y)
        X[:, 0] = X[:, 0] * 1000
        X[:, 1] = X[:, 1] / 1000
        lb = [-3000, -0.003]
        ub = [3000, 0.003]

        model = GPClassificationModel(lb=lb, ub=ub, inducing_size=20)

        model.fit(X[:50], y[:50])

        # pspace
        pm, _ = model.predict(X[:50], probability_space=True)
        pred = (pm > 0.5).numpy()
        npt.assert_allclose(pred, y[:50])

        # fspace
        pm, _ = model.predict(X[:50], probability_space=False)
        pred = (pm > 0).numpy()
        npt.assert_allclose(pred, y[:50])

        # smoke test update
        model.update(X, y)

        # pspace
        pm, _ = model.predict(X, probability_space=True)
        pred = (pm > 0.5).numpy()
        npt.assert_allclose(pred, y)

        # fspace
        pm, _ = model.predict(X, probability_space=False)
        pred = (pm > 0).numpy()
        npt.assert_allclose(pred, y)

    def test_predict_p(self):
        """
        Verify analytic p-space mean and var is correct.
        """
        X, y = self.X, self.y
        model = GPClassificationModel(
            torch.Tensor([-3]), torch.Tensor([3]), inducing_size=10
        )
        model.fit(X, y)

        pmean_analytic, pvar_analytic = model.predict(X, probability_space=True)

        fsamps = model.sample(X, 100000)
        psamps = norm.cdf(fsamps)
        pmean_samp = psamps.mean(0)
        pvar_samp = psamps.var(0)
        # TODO these tolerances are a bit loose, verify this is right.
        self.assertTrue(np.allclose(pmean_analytic, pmean_samp, atol=0.001))
        self.assertTrue(np.allclose(pvar_analytic, pvar_samp, atol=0.001))


class GPClassificationTest(unittest.TestCase):
    def test_1d_single_probit_new_interface(self):

        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 50
        n_opt = 1
        lb = -4.0
        ub = 4.0

        model_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=10),
                generator=OptimizeAcqfGenerator(
                    qUpperConfidenceBound, acqf_kwargs={"beta": 1.96}
                ),
                n_trials=n_opt,
            ),
        ]

        strat = SequentialStrategy(model_list)

        while not strat.finished:
            next_x = strat.gen()
            strat.add_data(next_x, [bernoulli.rvs(f_1d(next_x))])

        self.assertTrue(strat.y.shape[0] == n_init + n_opt)

        x = torch.linspace(-4, 4, 100)

        zhat, _ = strat.predict(x)

        # true max is 0, very loose test
        self.assertTrue(np.abs(x[np.argmax(zhat.detach().numpy())]) < 0.5)

    def test_1d_single_probit_batched(self):

        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 50
        n_opt = 2
        lb = -4.0
        ub = 4.0

        model_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=10),
                generator=OptimizeAcqfGenerator(
                    qUpperConfidenceBound, acqf_kwargs={"beta": 1.96}
                ),
                n_trials=n_opt,
            ),
        ]

        strat = SequentialStrategy(model_list)
        while not strat.finished:
            next_x = strat.gen(num_points=2)
            strat.add_data(next_x, bernoulli.rvs(f_1d(next_x)).squeeze())

        self.assertEqual(strat.y.shape[0], n_init + n_opt)

        x = torch.linspace(-4, 4, 100)

        zhat, _ = strat.predict(x)

        # true max is 0, very loose test
        self.assertTrue(np.abs(x[np.argmax(zhat.detach().numpy())]) < 0.5)

    def test_1d_single_probit(self):

        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 50
        n_opt = 1
        lb = -4.0
        ub = 4.0

        model_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=10),
                generator=OptimizeAcqfGenerator(
                    qUpperConfidenceBound, acqf_kwargs={"beta": 1.96}
                ),
                n_trials=n_opt,
            ),
        ]

        strat = SequentialStrategy(model_list)

        for _i in range(n_init + n_opt):
            next_x = strat.gen()
            strat.add_data(next_x, [bernoulli.rvs(f_1d(next_x))])

        x = torch.linspace(-4, 4, 100)

        zhat, _ = strat.predict(x)

        # true max is 0, very loose test
        self.assertTrue(np.abs(x[np.argmax(zhat.detach().numpy())]) < 0.5)

    def test_1d_single_probit_pure_exploration(self):

        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 50
        n_opt = 1
        lb = -4.0
        ub = 4.0

        strat_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=10),
                generator=OptimizeAcqfGenerator(
                    qUpperConfidenceBound, acqf_kwargs={"beta": 1.96}
                ),
                n_trials=n_opt,
            ),
        ]

        strat = SequentialStrategy(strat_list)

        for _i in range(n_init + n_opt):
            next_x = strat.gen()
            strat.add_data(next_x, [bernoulli.rvs(norm.cdf(next_x))])

        x = torch.linspace(-4, 4, 100)

        zhat, _ = strat.predict(x)

        # f(x) = x so we're just looking at corr between cdf(zhat) and cdf(x)
        self.assertTrue(
            pearsonr(norm.cdf(zhat.detach().numpy()).flatten(), norm.cdf(x).flatten())[
                0
            ]
            > 0.95
        )

    def test_2d_single_probit_pure_exploration(self):
        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 50
        n_opt = 1
        lb = [-1, -1]
        ub = [1, 1]

        strat_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=10),
                generator=OptimizeAcqfGenerator(
                    qUpperConfidenceBound, acqf_kwargs={"beta": 1.96}
                ),
                n_trials=n_opt,
            ),
        ]

        strat = SequentialStrategy(strat_list)

        for _i in range(n_init + n_opt):
            next_x = strat.gen()
            strat.add_data(next_x, [bernoulli.rvs(cdf_new_novel_det(next_x))])

        xy = np.mgrid[-1:1:30j, -1:1:30j].reshape(2, -1).T
        post_mean, _ = strat.predict(torch.Tensor(xy))
        phi_post_mean = norm.cdf(post_mean.reshape(30, 30).detach().numpy())

        phi_post_true = cdf_new_novel_det(xy)

        self.assertTrue(
            pearsonr(phi_post_mean.flatten(), phi_post_true.flatten())[0] > 0.9
        )

    def test_1d_single_targeting(self):

        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 50
        n_opt = 1
        lb = -4.0
        ub = 4.0

        target = 0.75

        def obj(x):
            return -((Normal(0, 1).cdf(x[..., 0]) - target) ** 2)

        strat_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=10),
                generator=OptimizeAcqfGenerator(
                    qUpperConfidenceBound, acqf_kwargs={"beta": 1.96}
                ),
                n_trials=n_opt,
            ),
        ]

        strat = SequentialStrategy(strat_list)

        for _i in range(n_init + n_opt):
            next_x = strat.gen()
            strat.add_data(next_x, [bernoulli.rvs(norm.cdf(next_x))])

        x = torch.linspace(-4, 4, 100)

        zhat, _ = strat.predict(x)

        # since target is 0.75, find the point at which f_est is 0.75
        est_max = x[np.argmin((norm.cdf(zhat.detach().numpy()) - 0.75) ** 2)]
        # since true z is just x, the true max is where phi(x)=0.75,
        self.assertTrue(np.abs(est_max - norm.ppf(0.75)) < 0.5)

    def test_1d_jnd(self):
        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 150
        n_opt = 1
        lb = -4.0
        ub = 4.0

        target = 0.5

        def obj(x):
            return -((Normal(0, 1).cdf(x[..., 0]) - target) ** 2)

        strat_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=10),
                generator=OptimizeAcqfGenerator(
                    qUpperConfidenceBound, acqf_kwargs={"beta": 1.96}
                ),
                n_trials=n_opt,
            ),
        ]

        strat = SequentialStrategy(strat_list)

        for _i in range(n_init + n_opt):
            next_x = strat.gen()
            strat.add_data(next_x, [bernoulli.rvs(norm.cdf(next_x / 1.5))])

        x = torch.linspace(-4, 4, 100)

        zhat, _ = strat.predict(x)

        # we expect jnd close to the target to be close to the correct
        # jnd (1.5), and since this is linear model this should be true
        # for both definitions of JND
        jnd_step = strat.get_jnd(grid=x[:, None], method="step")
        est_jnd_step = jnd_step[50]
        # looser test because step-jnd is hurt more by reverting to the mean
        self.assertTrue(np.abs(est_jnd_step - 1.5) < 0.5)

        jnd_taylor = strat.get_jnd(grid=x[:, None], method="taylor")
        est_jnd_taylor = jnd_taylor[50]
        self.assertTrue(np.abs(est_jnd_taylor - 1.5) < 0.25)

    def test_1d_single_lse(self):

        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 50
        n_opt = 1
        lb = -4.0
        ub = 4.0

        # target is in z space not phi(z) space, maybe that's
        # weird
        extra_acqf_args = {"target": 0.75, "beta": 1.96}

        strat_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=10),
                n_trials=n_opt,
                generator=OptimizeAcqfGenerator(
                    MCLevelSetEstimation, acqf_kwargs=extra_acqf_args
                ),
            ),
        ]

        strat = SequentialStrategy(strat_list)

        for _i in range(n_init + n_opt):
            next_x = strat.gen()
            strat.add_data(next_x, [bernoulli.rvs(norm.cdf(next_x))])

        x = torch.linspace(-4, 4, 100)

        zhat, _ = strat.predict(x)
        # since target is 0.75, find the point at which f_est is 0.75
        est_max = x[np.argmin((norm.cdf(zhat.detach().numpy()) - 0.75) ** 2)]
        # since true z is just x, the true max is where phi(x)=0.75,
        self.assertTrue(np.abs(est_max - norm.ppf(0.75)) < 0.5)

    def test_2d_single_probit(self):

        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 150
        n_opt = 1
        lb = [-1, -1]
        ub = [1, 1]

        strat_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=20),
                generator=OptimizeAcqfGenerator(
                    qUpperConfidenceBound, acqf_kwargs={"beta": 1.96}
                ),
                n_trials=n_opt,
            ),
        ]

        strat = SequentialStrategy(strat_list)

        for _i in range(n_init + n_opt):
            next_x = strat.gen()
            strat.add_data(next_x, [bernoulli.rvs(f_2d(next_x[None, :]))])

        xy = np.mgrid[-1:1:30j, -1:1:30j].reshape(2, -1).T
        zhat, _ = strat.predict(torch.Tensor(xy))

        self.assertTrue(np.all(np.abs(xy[np.argmax(zhat.detach().numpy())]) < 0.5))

    def test_extra_ask_warns(self):
        # test that when we ask more times than we have models, we warn but keep going
        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 3
        n_opt = 1
        lb = -4.0
        ub = 4.0

        model_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=10),
                generator=OptimizeAcqfGenerator(
                    qUpperConfidenceBound, acqf_kwargs={"beta": 1.96}
                ),
                n_trials=n_opt,
            ),
        ]

        strat = SequentialStrategy(model_list)

        for _i in range(n_init + n_opt):
            next_x = strat.gen()
            strat.add_data(next_x, [bernoulli.rvs(norm.cdf(f_1d(next_x)))])

        with self.assertWarns(RuntimeWarning):
            strat.gen()

    def test_1d_query(self):
        seed = 1
        torch.manual_seed(seed)
        np.random.seed(seed)
        n_init = 150
        n_opt = 1
        lb = -4.0
        ub = 4.0

        target = 0.5

        def obj(x):
            return -((Normal(0, 1).cdf(x[..., 0]) - target) ** 2)

        # Test sine function with period 4
        def test_fun(x):
            return np.sin(np.pi * x / 4)

        strat_list = [
            Strategy(
                lb=lb,
                ub=ub,
                n_trials=n_init,
                generator=SobolGenerator(lb=lb, ub=ub, seed=seed),
            ),
            Strategy(
                lb=lb,
                ub=ub,
                model=GPClassificationModel(lb=lb, ub=ub, inducing_size=10),
                generator=OptimizeAcqfGenerator(
                    qUpperConfidenceBound,
                    acqf_kwargs={"beta": 1.96, "objective": GenericMCObjective(obj)},
                ),
                n_trials=n_opt,
            ),
        ]

        strat = SequentialStrategy(strat_list)

        for _i in range(n_init + n_opt):
            next_x = strat.gen()
            strat.add_data(next_x, [bernoulli.rvs(norm.cdf(test_fun(next_x)))])

        # We expect the global max to be at (2, 1), the min at (-2, -1)
        fmax, argmax = strat.get_max()
        self.assertTrue(np.abs(fmax - 1) < 0.5)
        self.assertTrue(np.abs(argmax[0] - 2) < 0.5)

        fmin, argmin = strat.get_min()
        self.assertTrue(np.abs(fmin + 1) < 0.5)
        self.assertTrue(np.abs(argmin[0] + 2) < 0.5)

        # Query at x=2 should be f=1
        self.assertTrue(np.abs(strat.predict(torch.tensor([2]))[0] - 1) < 0.5)

        # Inverse query at val 1 should return (1,[2])
        val, loc = strat.inv_query(1.0, constraints={})
        self.assertTrue(np.abs(val - 1) < 0.5)
        self.assertTrue(np.abs(loc[0] - 2) < 0.5)

    def test_select_inducing_points(self):
        """Verify that when we have n_induc > data size, we use data as inducing,
        and otherwise we correctly select inducing points."""
        X, y = make_classification(
            n_samples=100,
            n_features=1,
            n_redundant=0,
            n_informative=1,
            random_state=1,
            n_clusters_per_class=1,
        )
        X, y = torch.Tensor(X), torch.Tensor(y)

        model = GPClassificationModel(
            torch.Tensor([-3]), torch.Tensor([3]), inducing_size=20
        )
        model.set_train_data(X[:10, ...], y[:10])

        # (inducing point selection sorts the inputs so we sort X to verify)
        self.assertTrue(
            np.allclose(
                model._select_inducing_points(method="auto"),
                X[:10].sort(0).values,
            )
        )

        model.set_train_data(X, y)

        self.assertTrue(len(model._select_inducing_points(method="auto")) <= 20)

        self.assertTrue(len(model._select_inducing_points(method="pivoted_chol")) <= 20)

        self.assertEqual(len(model._select_inducing_points(method="kmeans++")), 20)

        with self.assertRaises(AssertionError):
            model._select_inducing_points(method="12345")

    def test_hyperparam_consistency(self):
        # verify that creating the model `from_config` or with `__init__` has the same hyperparams

        m1 = GPClassificationModel(lb=[1, 2], ub=[3, 4])

        m2 = GPClassificationModel.from_config(
            config=Config(config_dict={"common": {"lb": "[1,2]", "ub": "[3,4]"}})
        )
        self.assertTrue(isinstance(m1.covar_module, type(m2.covar_module)))
        self.assertTrue(
            isinstance(m1.covar_module.base_kernel, type(m2.covar_module.base_kernel))
        )
        self.assertTrue(isinstance(m1.mean_module, type(m2.mean_module)))
        m1priors = list(m1.covar_module.named_priors())
        m2priors = list(m2.covar_module.named_priors())
        for p1, p2 in zip(m1priors, m2priors):
            name1, parent1, prior1, paramtransforms1, priortransforms1 = p1
            name2, parent2, prior2, paramtransforms2, priortransforms2 = p2
            self.assertTrue(name1 == name2)
            self.assertTrue(isinstance(parent1, type(parent2)))
            self.assertTrue(isinstance(prior1, type(prior2)))
            # no obvious way to test paramtransform equivalence


if __name__ == "__main__":
    unittest.main()
