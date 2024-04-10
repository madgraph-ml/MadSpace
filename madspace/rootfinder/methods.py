""" Numerical methods to find root"""

from typing import Callable, Optional
import torch
from torch import Tensor


MINKOWSKI = torch.diag(torch.tensor([1.0, -1.0, -1.0, -1.0]))
ITER = 100
XTOL = 2e-12
RTOL = 4 * torch.finfo(float).eps


def newton(
    f: Callable,
    df: Callable,
    a: float,
    b: float,
    x0: Optional[Tensor] = None,
    max_iter: int = ITER,
    epsilon=1e-8,
):
    if torch.any(f(a) * f(b) > 0):
        raise ValueError(f"None or no unique root in given intervall [{a},{b}]")

    # Define lower/upper boundaries as tensor
    xa = a * torch.ones_like(x0)
    xb = b * torch.ones_like(x0)

    # initilize guess
    if x0 is None:
        x0 = (xa + xb) / 2

    for i in range(max_iter):
        if torch.any(df(x0) < epsilon):
            raise ValueError("Derivative is too small")

        # do newtons-step
        x1 = x0 - f(x0) / df(x0)

        # check if within given intervall
        higher = x1 > xb
        lower = x1 < xa
        if torch.any(higher):
            x1[higher] = (xb[higher] + x0[higher]) / 2
        if torch.any(lower):
            x1[lower] = (xa[lower] + x0[lower]) / 2

        if torch.allclose(x0, x1, atol=XTOL, rtol=RTOL):
            print(f"coverged after {i} iterations")
            return x1

        # Adjust brackets
        low = f(x1) * f(xa) > 0
        xa[low] = x1[low]
        xb[~low] = x1[~low]

        x0 = x1

    print(f"not converged what?")
    return x0


def bisect(
    f: Callable,
    df: Callable,
    a: float,
    b: float,
    x0: Optional[Tensor] = None,
    max_iter: int = ITER,
):
    del df
    if torch.any(f(a) * f(b) > 0):
        raise ValueError(f"None or no unique root in given intervall [{a},{b}]")

    # Define lower/upper boundaries as tensor
    xa = a * torch.ones_like(x0)
    xb = b * torch.ones_like(x0)

    for i in range(max_iter):
        # define midpoint
        x1 = (xa + xb) / 2
        
        # adjusting brackets
        higher = f(x1) > 0
        xa[~higher] = x1[~higher]
        xb[higher] = x1[higher]

        if torch.allclose(xa, xb, atol=XTOL, rtol=RTOL):
            print(f"coverged after {i} iterations")
            return x1

        x0 = x1

    print(f"not converged what?")
    return x0
