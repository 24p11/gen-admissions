"""Flux de hasard séparés, tous dérivés d'une graine unique.

Pourquoi plusieurs flux ? Avec un seul générateur de hasard pour tout le
modèle, la moindre modification (par exemple un tirage supplémentaire dans
une règle d'hébergement) décale tous les tirages suivants et change les
arrivées, les durées et les parcours de tous les patients. Avec un flux par
usage, chaque partie du modèle garde ses propres tirages : on peut comparer
deux versions du modèle « toutes choses égales par ailleurs ».

Il n'y a toujours qu'une seule graine à enregistrer avec le jeu de données.
"""

from __future__ import annotations

import numpy as np

# Un flux par usage. L'ordre compte : il fixe quelle graine fille va à quel flux.
FLUX = (
    "arrivees_urgences",    # délais entre deux arrivées aux urgences
    "profils_urgences",     # âge, sexe, gravité des patients des urgences
    "issues_urgences",      # issue du passage et durée du passage
    "entrees_programmees",  # nombre et horaires des admissions programmées
    "parcours",             # catalogue, table de passage, premier service
    "durees",               # durées de séjour dans les unités
    "horaires",             # heures de sortie, effet week-end
    "population",           # retours de patients déjà connus
    "lits",                 # choix du service d'hébergement
)


class Hasard:
    """Fabrique et range les générateurs de nombres aléatoires du modèle.

    Utilisation : ``h = Hasard(42)`` puis ``h["durees"].lognormal(...)``.
    """

    def __init__(self, graine: int) -> None:
        self.graine = int(graine)
        racine = np.random.SeedSequence(self.graine)
        # La graine principale « bourgeonne » en graines filles indépendantes.
        filles = racine.spawn(len(FLUX))
        self._flux: dict[str, np.random.Generator] = {
            nom: np.random.default_rng(graine_fille)
            for nom, graine_fille in zip(FLUX, filles)
        }

    def __getitem__(self, nom: str) -> np.random.Generator:
        try:
            return self._flux[nom]
        except KeyError:  # pragma: no cover - erreur de programmation
            raise KeyError(f"Flux de hasard inconnu : {nom!r}. Flux disponibles : {FLUX}")
