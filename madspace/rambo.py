import numpy as np
from typing import Optional, Tuple
from scipy.optimize import brentq
from math import gamma, pi
import torch
from torch import Tensor, cos, sin, cosh, sinh, sqrt, log
import torch.functional as F

from .base import PhaseSpaceMapping
from .helper import (
    MINKOWSKI,
    map_fourvector_rambo,
    two_body_decay_factor,
    boost,
    boost_beam,
    rambo_func,
    newton,
    mass_func,
    lsquare,
)


class Rambo(PhaseSpaceMapping):
    """Rambo algorithm as presented in
    [1] Rambo [Comput. Phys. Commun. 40 (1986) 359-373]


    """

    def __init__(
        self,
        e_cm: float,
        nparticles: int,
        masses: list[float] = None,
    ):
        self.nparticles = nparticles
        self.e_cm = e_cm

        if masses is not None:
            self.masses = torch.tensor(masses)
            assert len(self.masses) == self.n_particles
            e_min = sum(masses)
        else:
            self.masses = masses
            e_min = 0.0

        if not self.e_cm >= e_min:
            raise ValueError(
                f"COM energy needs to be larger than sum of external"
            )

        dims_in = [(nparticles, 4)]
        dims_out = [(nparticles, 4)]
        super().__init__(dims_in, dims_out)

    def map(self, inputs, condition=None):
        del condition
        r = inputs[0]  # have dims (b,n,4)

        # Construct intermediate particle momenta
        q = map_fourvector_rambo(r)

        # sum over all particles
        Q = q.sum(dim=1, keepdim=True)  # has shape (b,1,4)

        # Get scaling factor and match dimensions
        M = sqrt(lsquare(Q))  # has shape (b,1)
        x = self.e_cm / M[..., None]  # has shape (b,1,1)

        # Boost and refactor
        p = boost(q, -Q)
        p = x * p

        gs = self.density(inputs)
        
        if self.masses is not None:
            # match dimensions of masses
            m = self.masses[None,...]

            # solve for xi in massive case, see Ref. [1]
            xi = torch.empty((r.shape[0], 1, 1))
            func = lambda x: mass_func(x, p, m, self.e_cm)
            df = lambda x: mass_func(x, p, m, self.e_cm, diff=True)
            guess = 0.5 * torch.ones((r.shape[0],))
            xi[:, 0, 0] = newton(func, df, 0.0, 1.0, guess)
            
            # Make them massive
            k = torch.ones_like(p)
            k[:, :, 0] = torch.sqrt(m**2 + xi[:, :, 0] ** 2 * p[:, :, 0] ** 2)
            k[:, :, 1:] = xi * p[:, :, 1:]

            # Get jacobian
            jac = self.weight(k, p, xi[:, 0, 0])

            return (k,), jac

        return (p,), gs

    def map_inverse(self, inputs, condition=None):
        """Does not exist for Rambo"""
        raise NotImplementedError
    
    def _weight(
        self,
        k: torch.Tensor,
        p: torch.Tensor,
        xi: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Args:
            k (torch.Tensor): massive momenta in shape=(b,n,4)
            p (torch.Tensor): massless momenta in shape=(b,n,4)
            xi (torch.Tensor, Optional): shift variable with shape=(b,)

        Returns:
            torch.Tensor: weight of sampler
        """
        # get volume for massless particles
        w0 = (
            (pi / 2.0) ** (self.n_particles - 1)
            * self.e_cm ** (2 * self.n_particles - 4)
            / (gamma(self.n_particles) * gamma(self.n_particles - 1))
        )

        if xi is not None:
            # get correction factor for massive ones
            ks = sqrt(k[:, :, 1] ** 2 + k[:, :, 2] ** 2 + k[:, :, 3] ** 2)
            ps = sqrt(p[:, :, 1] ** 2 + p[:, :, 2] ** 2 + p[:, :, 3] ** 2)
            k0 = k[:, :, 0]
            p0 = p[:, :, 0]
            w_M = (
                xi ** (3 * self.n_particles - 3)
                * torch.prod(p0 / k0, dim=1)
                * torch.sum(ps**2 / p0, dim=1)
                / torch.sum(ks**2 / k0, dim=1)
            )
            return w0 * w_M

        return w0

    def density(self, inputs, condition=None, inverse=False):
        if inverse:
            raise NotImplementedError

        xs = inputs[0]
        gs = torch.ones(xs.shape[0], dtype=xs.dtype, device=xs.device)
        vol = (
            (pi / 2.0) ** (self.nparticles - 1)
            * self.e_cm ** (2 * self.nparticles - 4)
            / (gamma(self.nparticles) * gamma(self.nparticles - 1))
        )
        return gs * vol
