""" Implement two-particle mappings.
    Bases on the mappings described in
    [1] https://freidok.uni-freiburg.de/data/154629
    [2] https://arxiv.org/abs/hep-ph/0008033
"""


from typing import Tuple, Optional
import torch
from torch import Tensor, sqrt, log, atan2

from .base import PhaseSpaceMapping, TensorList
from .helper import (
    two_particle_density,
    tinv_two_particle_density,
    kaellen,
    rotate_zy,
    inv_rotate_zy,
    boost,
    lsquare,
    edot,
    pi,
)
from .functional.tchannel import invt_to_costheta, costheta_to_invt
from .invariants import (
    BreitWignerInvariantBlock,
    UniformInvariantBlock,
    MasslessInvariantBlock,
    StableInvariantBlock,
)


class TwoParticleCOM(PhaseSpaceMapping):
    """
    Implement isotropic 2-particle phase-space, based on the mapping described in
        [2] https://arxiv.org/abs/hep-ph/0008033
        [3] https://freidok.uni-freiburg.de/data/154629
    """

    def __init__(
        self,
        m1: Tensor,
        m2: Tensor,
    ):
        """
        Args:
            m1 (Tensor): Mass of decay particle 1
            m2 (Tensor): Mass of decay particle 2
        """
        super().__init__(dims_in=[(2,), ()], dims_out=[(2, 4)], dims_c=None)
        self.m1 = m1
        self.m2 = m2

    def map(self, inputs: TensorList, condition=None):
        """Map from random numbers to momenta

        Args:
            inputs: list of two tensors [r, s]
                r: random numbers with shape=(b,2)
                s: squared COM energy with shape=(b,)

        Returns:
            p_decay (Tensor): decay momenta (lab frame) with shape=(b,2,4)
            det (Tensor): log det of mapping with shape=(b,)
        """
        del condition
        r, s = inputs[0], inputs[1]
        p1 = torch.zeros(r.shape[0], 4, device=r.device)
        p2 = torch.zeros(r.shape[0], 4, device=r.device)
        if torch.any(s < 0):
            raise ValueError(f"s needs to be always positive")

        r1, r2 = r[:, 0], r[:, 1]

        # Define the angles
        phi = 2 * pi * r1
        costheta = 2 * r2 - 1

        # Define the momenta (in COM frame of decaying particle)
        p1[:, 0] = (s + self.m1**2 - self.m2**2) / (2 * sqrt(s))
        p2[:, 0] = (s + self.m2**2 - self.m1**2) / (2 * sqrt(s))
        p1[:, 3] = sqrt(kaellen(s, self.m1**2, self.m2**2)) / (2 * sqrt(s))

        # Rotate and define p2 spatial components
        p1 = rotate_zy(p1, phi, costheta)
        p2[:, 1:] = -p1[:, 1:]

        # get the density and decay momenta
        # (C.10) in [2] == (C.8)/(4PI)
        gs = two_particle_density(s, self.m1, self.m2) / (4 * pi)
        p_decay = torch.stack([p1, p2], dim=1)

        return (p_decay,), 1 / gs

    def map_inverse(self, inputs: TensorList, condition=None):
        """Inverse map from decay momenta onto random numbers

        Args:
            inputs (TensorList): list with only one tensor [p_decay]
                p_decay: decay momenta (lab frame) with shape=(b,2,4)

        Returns:
            r (Tensor): random numbers with shape=(b,2)
            det (Tensor): log det of mapping with shape=(b,)
        """
        del condition
        p_decay = inputs[0]
        # Decaying particle in lab-frame
        p0 = p_decay.sum(dim=1)
        s = lsquare(p0)
        p1 = p_decay[:, 0]

        # get p1 absolute momentum
        p1mag = sqrt(edot(p1[:, 1:], p1[:, 1:]))

        # Extract phi and theta
        costheta = p1[:, 3] / p1mag
        phi = atan2(p1[:, 2], p1[:, 1])

        # Get the random numbers
        r1 = phi / 2 / pi
        r2 = (costheta + 1) / 2
        r = torch.stack([r1, r2], dim=1)

        # get the density and decay momenta
        # (C.10) in [2] == (C.8)/(4PI)
        gs = two_particle_density(s, self.m1, self.m2) / (4 * pi)

        return (r, s), gs

    def density(self, inputs: TensorList, condition=None, inverse=False):
        del condition
        if inverse:
            s = inputs[1]
            gs = two_particle_density(s, self.m1, self.m2) / (4 * pi)
            return gs

        s = lsquare(inputs[0].sum(dim=1))
        gs = two_particle_density(s, self.m1, self.m2) / (4 * pi)
        return 1 / gs


class TwoParticleLAB(PhaseSpaceMapping):
    """
    Implement isotropic 2-particle phase-space, based on the mapping described in
        [2] https://arxiv.org/abs/hep-ph/0008033
        [3] https://freidok.uni-freiburg.de/data/154629
    """

    def __init__(
        self,
        m1: Tensor,
        m2: Tensor,
    ):
        super().__init__(dims_in=[(2,), (4,)], dims_out=[(2, 4)], dims_c=None)
        self.m1 = m1  # Mass of decay particle 1
        self.m2 = m2  # Mass of decay particle 2

    def map(self, inputs: TensorList, condition=None):
        """Map from random numbers to momenta

        Args:
            inputs (TensorList): list of two tensors [r, p0]
                r: random numbers with shape=(b,2)
                p0: total momentum in lab frame with shape=(b,4)

        Returns:
            p_decay (Tensor): decay momenta (lab frame) with shape=(b,2,4)
            det (Tensor): det of mapping with shape=(b,)
        """
        del condition
        r, p0 = inputs[0], inputs[1]
        p1 = torch.zeros(r.shape[0], 4, device=r.device)
        s = lsquare(p0)

        with torch.no_grad():
            if torch.any(s < 0):
                raise ValueError(f"s needs to be always positive")

        r1, r2 = r[:, 0], r[:, 1]

        # Define the angles
        phi = 2 * pi * r1
        costheta = 2 * r2 - 1

        # Define the momenta (in COM frame of decaying particle)
        p1[:, 0] = (s + self.m1**2 - self.m2**2) / (2 * sqrt(s))
        p1[:, 3] = sqrt(kaellen(s, self.m1**2, self.m2**2)) / (2 * sqrt(s))

        # First rotate, then boost into lab-frame
        p1 = rotate_zy(p1, phi, costheta)
        p1 = boost(p1, p0)
        p2 = p0 - p1

        # get the density and decay momenta
        gs = two_particle_density(s, self.m1, self.m2) / (4 * pi)
        p_decay = torch.stack([p1, p2], dim=1)

        return (p_decay,), 1 / gs

    def map_inverse(self, inputs: TensorList, condition=None):
        """Inverse map from decay momenta onto random numbers

        Args:
            inputs (TensorList): list with only one tensor [p_decay]
                p_decay: decay momenta (lab frame) with shape=(b,2,4)

        Returns:
            r (Tensor): random numbers with shape=(b,2)
            det (Tensor): log det of mapping with shape=(b,)
        """
        del condition
        p_decay = inputs[0]

        # Decaying particle in lab-frame
        p0 = p_decay.sum(dim=1)
        s = lsquare(p0)
        p1 = p_decay[:, 0]

        # Boost p1 into COM
        p1 = boost(p1, p0, inverse=True)

        # get p1 absolute momentum
        p1mag = sqrt(edot(p1[:, 1:], p1[:, 1:]))

        # Extract phi and theta
        costheta = p1[:, 3] / p1mag
        phi = atan2(p1[:, 2], p1[:, 1])

        # Get the random numbers
        r1 = phi / 2 / pi
        r2 = (costheta + 1) / 2
        r = torch.stack([r1, r2], dim=1)

        # get the density
        gs = two_particle_density(s, self.m1, self.m2) / (4 * pi)

        return (r, p0), gs

    def density(self, inputs: TensorList, condition=None, inverse=False):
        del condition
        if inverse:
            s = lsquare(inputs[1])
            gs = two_particle_density(s, self.m1, self.m2) / (4 * pi)
            return gs

        s = lsquare(inputs[0].sum(dim=1))
        gs = two_particle_density(s, self.m1, self.m2) / (4 * pi)
        return 1 / gs


class tInvariantTwoParticleCOM(PhaseSpaceMapping):
    """
    Implement anisotropic decay, based on the mapping described in
        [1] https://arxiv.org/abs/hep-ph/0008033
        [2] https://freidok.uni-freiburg.de/data/154629
    """

    def __init__(
        self,
        mt: Optional[Tensor] = None,
        wt: Optional[Tensor] = None,
        nu: float = 1.4,
        flat: bool = False,
    ):
        super().__init__(
            dims_in=[(2,), (2, 4), (2,)],
            dims_out=[(2, 4), (2,), (2,)],
            dims_c=None,
        )

        # Define which t-mapping is used
        # MadGraph only uses flat mappings for t? (Check with Olivio)
        if flat:
            self.t_map = UniformInvariantBlock()
        elif mt is None:
            self.t_map = MasslessInvariantBlock(nu=nu)
        elif wt is None:
            self.t_map = StableInvariantBlock(mass=mt, nu=nu)
        else:
            self.t_map = BreitWignerInvariantBlock(mass=mt, width=wt)

    def map(self, inputs: TensorList, condition=None):
        del condition
        r, p_in, m_out = inputs[0], inputs[1], inputs[2]

        # Extract random numbers, input momenta and output masses
        r1, r2 = r[:, 0], r[:, 1]
        ptot = p_in.sum(dim=1)
        p1 = p_in[:, 0]
        p2 = p_in[:, 1]

        # get invariants and incoming virtualities
        p1_2 = lsquare(p1)
        p2_2 = lsquare(p2)
        s = lsquare(ptot)

        # Define outgoing momenta and extract their masses/virtualities
        k1 = torch.zeros(r.shape[0], 4, device=r.device)
        m1 = m_out[:, 0]
        m2 = m_out[:, 1]

        # Get phi angle
        phi = 2 * r1 * pi

        # get t_min and max
        cos_min = (-1.0) * torch.ones_like(p1)
        cos_max = (+1.0) * torch.ones_like(p1)
        tmin = costheta_to_invt(s, p1_2, p2_2, m1, m2, cos_min)
        tmax = costheta_to_invt(s, p1_2, p2_2, m1, m2, cos_max)
        (t,), gs_inv = self.t_map.map([r2], condition=[-tmax, -tmin])

        # Map from t to cos_theta (needs -t as it samples |t|)
        costheta = invt_to_costheta(s, p1_2, p2_2, m1, m2, -t)

        # Define the momenta (in COM frame of decaying particle)
        k1[:, 0] = (s + m1**2 - m2**2) / (2 * sqrt(s))
        k1[:, 3] = sqrt(kaellen(s, m1**2, m2**2)) / (2 * sqrt(s))

        # Then rotate twice (within COM and into p1-axis)
        p1mag = sqrt(edot(p1[:, 1:], p1[:, 1:]))
        phi1 = atan2(p1[:, 2], p1[:, 1])
        costheta1 = p1[:, 3] / p1mag
        k1 = rotate_zy(k1, phi, costheta)
        k1 = rotate_zy(k1, phi1, costheta1)
        k2 = ptot - k1

        # get the density and decay momenta
        gp = tinv_two_particle_density(s, p1_2, p2_2) / (2 * pi)
        p_out = torch.stack([k1, k2], dim=1)
        q2_in = torch.stack([p1_2, p2_2], dim=1)
        angles_in = torch.stack([phi1, costheta1], dim=1)

        return (p_out, q2_in, angles_in), gs_inv / gp

    def map_inverse(self, inputs: TensorList, condition=None):
        del condition
        p_out, q2_in, angles_in = inputs[0]

        # Extraxt individual momenta
        k1 = p_out[:, 0]
        k2 = p_out[:, 1]
        phi1 = angles_in[:, 0]
        costheta1 = angles_in[:, 1]
        ptot = p_out.sum(dim=1)

        # get invariants and outgoing masses
        m1 = sqrt(lsquare(k1))
        m2 = sqrt(lsquare(k2))
        m_out = torch.stack([m1, m2], dim=1)
        s = lsquare(ptot)

        # Define incoming momenta and extract their virtualities
        p1 = torch.zeros(p_out.shape[0], 4, device=p_out.device)
        p1_2 = q2_in[:, 0]
        p2_2 = q2_in[:, 1]

        # Define the momenta (in COM frame of decaying particle)
        p1[:, 0] = (s + p1_2 - p2_2) / (2 * sqrt(s))
        p1[:, 3] = sqrt(kaellen(s, p1_2, p2_2)) / (2 * sqrt(s))
        p1 = rotate_zy(p1, phi1, costheta1)
        p2 = ptot - p1

        # calculate k1 absolute momentum
        k1mag = sqrt(edot(k1[:, 1:], k1[:, 1:]))

        # get rotate away angles from incoming momenta
        # and then extract phi and theta
        k1 = inv_rotate_zy(k1, phi1, costheta1)
        costheta = k1[:, 3] / k1mag
        phi = atan2(k1[:, 2], k1[:, 1])

        # Map from cos_theta to t
        t = costheta_to_invt(s, p1_2, p2_2, m1, m2, costheta)

        # Get the random numbers
        r1 = phi / 2 / pi
        cos_min = (-1.0) * torch.ones_like(k1)
        cos_max = (+1.0) * torch.ones_like(k1)
        tmin = costheta_to_invt(s, p1_2, p2_2, m1, m2, cos_min)
        tmax = costheta_to_invt(s, p1_2, p2_2, m1, m2, cos_max)
        (r2,), gs_inv = self.t_map.map_inverse([-t], condition=[-tmax, -tmin])
        r = torch.stack([r1, r2], dim=1)

        # get the density and output momenta
        gp = tinv_two_particle_density(s, p1_2, p2_2) / (2 * pi)
        p_in = torch.stack([p1, p2], dim=1)

        return (r, p_in, m_out), gs_inv * gp

    def density(self, inputs: TensorList, condition=None, inverse=False):
        """Returns the density only of the mapping"""
        if inverse:
            _, density = self.map_inverse(inputs, condition)
            return density
        _, density = self.map(inputs, condition)
        return density


class tInvariantTwoParticleLAB(PhaseSpaceMapping):
    """
    Implement anisotropic decay, based on the mapping described in
        [1] https://arxiv.org/abs/hep-ph/0008033
        [2] https://freidok.uni-freiburg.de/data/154629
    """

    def __init__(
        self,
        mt: Optional[Tensor] = None,
        wt: Optional[Tensor] = None,
        nu: float = 1.4,
        flat: bool = False,
    ):
        super().__init__(
            dims_in=[(2,), (2, 4), (2,)],
            dims_out=[(2, 4), (2,), (2,)],
            dims_c=None,
        )

        # Define which t-mapping is used
        # MadGraph only uses flat mappings for t!
        if flat:
            self.t_map = UniformInvariantBlock()
        elif mt is None:
            self.t_map = MasslessInvariantBlock(nu=nu)
        elif wt is None:
            self.t_map = StableInvariantBlock(mass=mt, nu=nu)
        else:
            self.t_map = BreitWignerInvariantBlock(mass=mt, width=wt)

    def map(self, inputs: TensorList, condition=None):
        del condition
        r, p_in, m_out = inputs[0], inputs[1], inputs[2]

        # Extract random numbers, input momenta and output masses
        r1, r2 = r[:, 0], r[:, 1]
        ptot = p_in.sum(dim=1)
        p1 = p_in[:, 0]
        p2 = p_in[:, 1]
        p1_com = boost(p1, ptot, inverse=True)

        # get invariants and incoming virtualities
        p1_2 = lsquare(p1)
        p2_2 = lsquare(p2)
        s = lsquare(ptot)

        # Define outgoing momenta and extract their masses/virtualities
        k1 = torch.zeros(r.shape[0], 4, device=r.device)
        m1 = m_out[:, 0]
        m2 = m_out[:, 1]

        # Get phi angle
        phi = 2 * r1 * pi

        # get t_min and max
        cos_min = (-1.0) * torch.ones_like(p1)
        cos_max = (+1.0) * torch.ones_like(p1)
        tmin = costheta_to_invt(s, p1_2, p2_2, m1, m2, cos_min)
        tmax = costheta_to_invt(s, p1_2, p2_2, m1, m2, cos_max)
        (t,), gs_inv = self.t_map.map([r2], condition=[-tmax, -tmin])

        # Map from t to cos_theta (needs -t as it samples |t|)
        costheta = invt_to_costheta(s, p1_2, p2_2, m1, m2, -t)

        # Define the momenta (in COM frame of decaying particle)
        k1[:, 0] = (s + m1**2 - m2**2) / (2 * sqrt(s))
        k1[:, 3] = sqrt(kaellen(s, m1**2, m2**2)) / (2 * sqrt(s))

        # Then rotate twice (within COM and into p1-axis)
        p1mag = sqrt(edot(p1_com[:, 1:], p1_com[:, 1:]))
        phi1 = atan2(p1_com[:, 2], p1_com[:, 1])
        costheta1 = p1_com[:, 3] / p1mag
        k1 = rotate_zy(k1, phi, costheta)
        k1 = rotate_zy(k1, phi1, costheta1)
        k1 = boost(k1, ptot)
        k2 = ptot - k1

        # get the density and outputs
        gp = tinv_two_particle_density(s, p1_2, p2_2) / (2 * pi)
        p_out = torch.stack([k1, k2], dim=1)
        q2_in = torch.stack([p1_2, p2_2], dim=1)
        angles_in = torch.stack([phi1, costheta1], dim=1)

        return (p_out, q2_in, angles_in), gs_inv / gp

    def map_inverse(self, inputs: TensorList, condition=None):
        del condition
        p_out, q2_in, angles_in = inputs[0]

        # Extraxt individual momenta
        k1 = p_out[:, 0]
        k2 = p_out[:, 1]
        phi1 = angles_in[:, 0]
        costheta1 = angles_in[:, 1]
        ptot = p_out.sum(dim=1)

        # get invariants and outgoing masses
        m1 = sqrt(lsquare(k1))
        m2 = sqrt(lsquare(k2))
        m_out = torch.stack([m1, m2], dim=1)
        s = lsquare(ptot)

        # Define incoming momenta and extract their virtualities
        p1 = torch.zeros(p_out.shape[0], 4, device=p_out.device)
        p1_2 = q2_in[:, 0]
        p2_2 = q2_in[:, 1]

        # Define the momenta (in COM frame of decaying particle)
        p1[:, 0] = (s + p1_2 - p2_2) / (2 * sqrt(s))
        p1[:, 3] = sqrt(kaellen(s, p1_2, p2_2)) / (2 * sqrt(s))
        p1 = rotate_zy(p1, phi1, costheta1)
        p1 = boost(p1, ptot)
        p2 = ptot - p1

        # boost inverse, rotate back
        # and then extract phi and theta
        k1 = boost(p1, ptot, inverse=True)
        k1 = inv_rotate_zy(k1, phi1, costheta1)
        k1mag = sqrt(edot(k1[:, 1:], k1[:, 1:]))
        costheta = k1[:, 3] / k1mag
        phi = atan2(k1[:, 2], k1[:, 1])

        # Map from cos_theta to t
        cos_min = (-1.0) * torch.ones_like(k1)
        cos_max = (+1.0) * torch.ones_like(k1)
        tmin = costheta_to_invt(s, p1_2, p2_2, m1, m2, cos_min)
        tmax = costheta_to_invt(s, p1_2, p2_2, m1, m2, cos_max)
        t = costheta_to_invt(s, p1_2, p2_2, m1, m2, costheta)

        # Get the random numbers
        r1 = phi / 2 / pi
        (r2,), gs_inv = self.t_map.map_inverse([-t], condition=[-tmax, -tmin])
        r = torch.stack([r1, r2], dim=1)

        # get the density and output momenta
        gp = tinv_two_particle_density(s, p1_2, p2_2) / (2 * pi)
        p_in = torch.stack([p1, p2], dim=1)

        return (r, p_in, m_out), gs_inv * gp

    def density(self, inputs: TensorList, condition=None, inverse=False):
        """Returns the density only of the mapping"""
        if inverse:
            _, density = self.map_inverse(inputs, condition)
            return density
        _, density = self.map(inputs, condition)
        return density
