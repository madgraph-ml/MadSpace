import numpy as np
from typing import Optional, Tuple
from math import gamma, pi
import torch
from torch import Tensor, cos, sin, cosh, sinh, sqrt, log, sum, atan2
import torch.functional as F
import sys

from .rootfinder.roots import get_u_parameter, get_xi_parameter


from .base import PhaseSpaceMapping, TensorList
from .helper import (
    MINKOWSKI,
    map_fourvector_rambo,
    map_fourvector_rambo_diet,
    two_body_decay_factor,
    boost,
    boost_beam,
    edot,
    lsquare,
)


class Rambo(PhaseSpaceMapping):
    """Rambo algorithm as presented in
    [1] Rambo [Comput. Phys. Commun. 40 (1986) 359-373]

    Note: we make ``e_cm`` an input instead of a fixed initialization
          parameter to make it compatible for event sampling
          for hadron colliders.
    """

    def __init__(
        self,
        nparticles: int,
        masses: list[float] = None,
    ):
        self.nparticles = nparticles

        if masses is not None:
            self.masses = torch.tensor(masses)
            assert len(self.masses) == self.nparticles
            self.e_min = sum(masses)
        else:
            self.masses = masses
            self.e_min = 0.0

        dims_in = [(4 * nparticles,), ()]
        dims_out = [(nparticles, 4)]
        super().__init__(dims_in, dims_out)

    def map(self, inputs: TensorList, condition: TensorList = None):
        del condition
        r = inputs[0]  # has dims (b,4*n)
        e_cm = inputs[1]  # has dims (b,) or ()

        # reshape random numbers to future shape (b,n,4)
        r = r.reshape((-1, self.nparticles, 4))

        # Check if the partonic COM energy is large enough
        with torch.no_grad():
            if torch.any(e_cm <= self.e_min):
                raise ValueError(
                    f"partonic COM energy needs to be larger than sum of external masses!"
                )

        # Construct intermediate particle momenta
        q = map_fourvector_rambo(r)

        # sum over all particles
        Q = q.sum(dim=1, keepdim=True)  # has shape (b,1,4)

        # Get scaling factor and match dimensions
        M = sqrt(lsquare(Q))  # has shape (b,1)
        x = e_cm / M  # has shape (b,1)

        # Boost and refactor
        p = boost(q, Q, inverse=True)
        p = x[..., None] * p

        torch_ones = torch.ones((r.shape[0],))
        w0 = torch_ones * self._massles_weight(e_cm)

        if self.masses is not None:
            # match dimensions of masses
            m = self.masses[None, ...]

            # solve for xi in massive case, see Ref. [1]
            xi = get_xi_parameter(p[:, :, 0], m)

            # Make momenta massive
            xi = xi[:, None, None]
            k = torch.ones_like(p)
            k[:, :, 0] = torch.sqrt(m**2 + xi[:, :, 0] ** 2 * p[:, :, 0] ** 2)
            k[:, :, 1:] = xi * p[:, :, 1:]

            # Get massive density
            w_m = self._massive_weight(k, p, xi[:, 0, 0])

            return (k,), w_m * w0

        return (p,), w0

    def map_inverse(self, inputs, condition=None):
        """Does not exist for Rambo"""
        raise NotImplementedError

    def _massles_weight(self, e_cm):
        w0 = (
            (pi / 2.0) ** (self.nparticles - 1)
            * e_cm ** (2 * self.nparticles - 4)
            / (gamma(self.nparticles) * gamma(self.nparticles - 1))
        )
        return w0

    def _massive_weight(
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
            torch.Tensor: massive weight
        """
        # get correction factor for massive ones
        ks2 = k[:, :, 1] ** 2 + k[:, :, 2] ** 2 + k[:, :, 3] ** 2
        ps2 = p[:, :, 1] ** 2 + p[:, :, 2] ** 2 + p[:, :, 3] ** 2
        k0 = k[:, :, 0]
        p0 = p[:, :, 0]
        w_M = (
            xi ** (3 * self.nparticles - 3)
            * torch.prod(p0 / k0, dim=1)
            * torch.sum(ps2 / p0, dim=1)
            / torch.sum(ks2 / k0, dim=1)
        )
        return w_M

    def density(self, inputs, condition=None, inverse=False):
        del condition
        if inverse:
            raise NotImplementedError

        _, gs = self.map(self, inputs)
        return gs


class RamboOnDiet(PhaseSpaceMapping):
    """Rambo on Diet algorithm as presented in
        [1] Rambo on diet - https://arxiv.org/abs/1308.2922

    Note, that here is an error in the algorithm of [1], which has been fixed.
    For details see:
        [2] RW's PhD thesis - https://doi.org/10.11588/heidok.00029154
        [3] ELSA paper - https://arxiv.org/abs/2305.07696
    """

    def __init__(
        self,
        nparticles: int,
        masses: list[float] = None,
    ):
        self.nparticles = nparticles

        if masses is not None:
            self.masses = torch.tensor(masses)
            assert len(self.masses) == self.nparticles
            self.e_min = sum(masses)
        else:
            self.masses = masses
            self.e_min = 0.0

        dims_in = [(3 * nparticles - 4,), ()]
        dims_out = [(nparticles, 4)]

        # For splitting into costheta and phi
        self.angular_mask = torch.tensor([True, False]).repeat(nparticles - 1)

        super().__init__(dims_in, dims_out)

    def map(self, inputs: TensorList, condition: TensorList = None):
        del condition
        r = inputs[0]  # has dims (b,3*n-4)
        e_cm = inputs[1]  # has dims (b,) or ()

        # Check if the partonic COM energy is large enough
        with torch.no_grad():
            if torch.any(e_cm <= self.e_min):
                raise ValueError(
                    f"partonic COM energy needs to be larger than sum of external masses!"
                )

        # Do rambo on diet loop
        # prepare momenta
        p = torch.empty((r.shape[0], self.nparticles, 4))
        k = torch.empty((r.shape[0], self.nparticles, 4))

        # split random numbers in energy and angular
        ru, romega = r[:, : self.nparticles - 2], r[:, self.nparticles - 2 :]

        # Solve rambo equation numerically for all u directly
        # u has shape=(b, nparticles - 2)
        u = get_u_parameter(ru)

        # split into rcos and rphi
        rcos = romega[:, self.angular_mask]
        rphi = romega[:, ~self.angular_mask]

        # Define all angles vectorized
        cos_theta = 2 * rcos - 1
        phi = 2 * pi * rphi

        # Define intermediate masses
        M = torch.zeros((r.shape[0], self.nparticles))
        M[:, 0] = e_cm
        M[:, 1:-1] = torch.cumprod(u, dim=1) * e_cm

        # Define first n-1 energies
        # gets shape (b, nparticles - 1)
        q = 4 * M[:, :-1] * two_body_decay_factor(M[:, :-1], M[:, 1:], 0)

        # Define first (n-1) particles
        pnm1 = map_fourvector_rambo_diet(q, cos_theta, phi)

        # Define Qs
        Q = e_cm * torch.tile(torch.tensor([1, 0, 0, 0]), (r.shape[0], 1))

        # Define loop over (n-1 particles) boosts
        for i in range(self.nparticles - 1):
            # Define Qi
            Q0_i = sqrt(q[:, i] ** 2 + M[:, i + 1] ** 2)
            Qp_i = -pnm1[:, i, 1:]
            Q_i = torch.concat([Q0_i[:, None], Qp_i], dim=1)

            # Boost p_i and Q_i along Q
            p[:, i] = boost(pnm1[:, i], Q)
            Q = boost(Q_i, Q)

        # Define final particle
        p[:, self.nparticles - 1] = Q

        # Get massless phase-space weights
        torch_ones = torch.ones((r.shape[0],))
        w0 = torch_ones * self._massles_weight(e_cm)

        if self.masses is not None:
            # match dimensions of masses
            m = self.masses[None, ...]

            # solve for xi in massive case, see Ref. [1]
            xi = get_xi_parameter(p[:, :, 0], m)

            # Make momenta massive
            xi = xi[:, None, None]
            k = torch.ones_like(p)
            k[:, :, 0] = sqrt(m**2 + xi[:, :, 0] ** 2 * p[:, :, 0] ** 2)
            k[:, :, 1:] = xi * p[:, :, 1:]

            # Get massive density corr. factor
            w_m = self._massive_weight(k, p, xi[:, 0, 0])

            return (k,), w_m * w0

        return (p,), w0

    def map_inverse(self, inputs: TensorList, condition=None):
        # Get input momenta
        k = inputs[0]
        e_cm = sqrt(lsquare(k.sum(dim=1)))
        w0 = self._massles_weight(e_cm)

        # Make momenta massless before going back to random numbers
        p = torch.empty((k.shape[0], self.n_particles, 4))
        if self.masses is not None:
            # Define masses
            m = self.masses[None, ...]

            # solve for xi in massive case, see Ref. [1], here analytic result possible!
            xi = sum(sqrt(k[:, :, 0] ** 2 - m**2), dim=-1) / e_cm

            # Make them massless
            xi = xi[:, None, None]
            p[:, :, 0] = torch.sqrt(k[:, :, 0] ** 2 - m**2) / xi[:, :, 0]
            p[:, :, 1:] = k[:, :, 1:] / xi
            wm = self._massive_weight(k, pi, xi)

        else:
            xi = None
            wm = 1.0
            p[:, :, 0] = k[:, :, 0]
            p[:, :, 1:] = k[:, :, 1:]

        # Construct random numbers
        M = torch.empty(k.shape[0])
        M_prev = torch.empty(k.shape[0])
        Q = torch.empty((k.shape[0], 4))
        r = torch.empty((k.shape[0], 3 * self.n_particles - 4))

        # Assign last particle
        P = torch.cumsum(p[:, :-1], dim=1).flip(1)
        M = lsquare(P)  # has shape (b, n-1)
        u = M[:, :-1] / M[:, 1:]
        iarray = torch.arange(1, self.nparticles)[None, :]
        uc = self.nparticles + 1 - iarray
        uexp = 2 * (self.nparticles - iarray)
        ru = uc * u**uexp - (uc - 1) * u ** (uexp + 2)

        romega = torch.empty((k.shape[0], 2 * self.nparticles - 2))
        # Has shape=(b,n)
        Q[:] = p[:, -1]
        for i in range(self.n_particles, 1, -1):
            Q += p[:, i - 2]
            p_prime = boost(p[:, i - 2], Q, inverse=True)
            pmag = sqrt(edot(p_prime[:, 1:]))
            costheta = p_prime[:, 3] / pmag
            romega[:, self.angular_mask] = 0.5 * costheta + 1.0
            phi = atan2(p_prime[:, 2], p_prime[:, 1])
            romega[:, ~self.angular_mask] = phi / (2 * pi) + (phi < 0)

        # Concat angular and energy random numbers
        r = torch.cat([ru, romega], dim=-1)
        return (r, e_cm), 1 / wm / w0

    def _massles_weight(self, e_cm):
        w0 = (
            (pi / 2.0) ** (self.nparticles - 1)
            * e_cm ** (2 * self.nparticles - 4)
            / (gamma(self.nparticles) * gamma(self.nparticles - 1))
        )
        return w0

    def _massive_weight(
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
            torch.Tensor: massive weight
        """
        # get correction factor for massive ones
        ks2 = k[:, :, 1] ** 2 + k[:, :, 2] ** 2 + k[:, :, 3] ** 2
        ps2 = p[:, :, 1] ** 2 + p[:, :, 2] ** 2 + p[:, :, 3] ** 2
        k0 = k[:, :, 0]
        p0 = p[:, :, 0]
        w_M = (
            xi ** (3 * self.nparticles - 3)
            * torch.prod(p0 / k0, dim=1)
            * torch.sum(ps2 / p0, dim=1)
            / torch.sum(ks2 / k0, dim=1)
        )
        return w_M

    def density(self, inputs, condition=None, inverse=False):
        del condition
        if inverse:
            raise NotImplementedError

        _, gs = self.map(self, inputs)
        return gs
