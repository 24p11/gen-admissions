"""Outils de tirage au hasard : loi log-normale, choix pondéré, profils horaires."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

# Écart entre le 3e et le 1er quartile d'une loi normale réduite (≈ 1,349).
_LARGEUR_QUARTILES = 2 * norm.ppf(0.75)


def parametres_lognormale(mediane: float, q1: float, q3: float) -> tuple[float, float]:
    """Déduit (mu, sigma) d'une loi log-normale à partir de la médiane et des quartiles.

    Le logarithme de la médiane donne le centre de la cloche ; l'écart entre
    les logarithmes des quartiles donne sa largeur.
    """
    if not (0 < q1 <= mediane <= q3):
        raise ValueError(f"Quartiles incohérents : q1={q1}, médiane={mediane}, q3={q3}")
    mu = float(np.log(mediane))
    if q3 == q1:
        sigma = 0.05  # distribution quasi constante
    else:
        sigma = float((np.log(q3) - np.log(q1)) / _LARGEUR_QUARTILES)
    return mu, max(sigma, 0.05)


@dataclass(frozen=True)
class LoiDuree:
    """Loi de la durée de passage dans une unité (en jours).

    - ``part_moins_1j`` : proportion de passages de moins d'un jour, dont la
      durée est tirée en heures (entre ``heures_min`` et ``heures_max``) ;
    - les autres passages suivent une loi log-normale (``mu``, ``sigma``)
      tronquée à 1 jour minimum.
    """

    mu: float
    sigma: float
    part_moins_1j: float
    heures_min: float = 2.0
    heures_max: float = 20.0

    @classmethod
    def depuis_quartiles(cls, mediane: float, q1: float, q3: float, part_moins_1j: float) -> "LoiDuree":
        mu, sigma = parametres_lognormale(mediane, q1, q3)
        return cls(mu=mu, sigma=sigma, part_moins_1j=float(part_moins_1j))

    def tirer_heures(self, rng: np.random.Generator) -> float:
        """Tire une durée en heures."""
        if rng.random() < self.part_moins_1j:
            return float(rng.uniform(self.heures_min, self.heures_max))
        jours = float(rng.lognormal(self.mu, self.sigma))
        return max(jours, 1.0) * 24.0


def choix_pondere(rng: np.random.Generator, valeurs: list, poids) -> object:
    """Tire une valeur avec une probabilité proportionnelle à son poids."""
    p = np.asarray(poids, dtype=float)
    total = p.sum()
    if total <= 0:
        raise ValueError("Tous les poids sont nuls")
    return valeurs[int(rng.choice(len(valeurs), p=p / total))]


class ProfilHoraire:
    """Poids par heure (0-23), normalisés, pour tirer une heure de la journée."""

    def __init__(self, poids_par_heure: dict[int, float]) -> None:
        p = np.zeros(24)
        for h, w in poids_par_heure.items():
            p[int(h)] = float(w)
        if p.sum() <= 0:
            raise ValueError("Profil horaire vide")
        self.poids = p / p.sum()

    def tirer_heure(self, rng: np.random.Generator) -> float:
        """Tire une heure décimale (par exemple 11,37) selon le profil."""
        heure = int(rng.choice(24, p=self.poids))
        return heure + float(rng.random())
