"""Graphiques communs aux notebooks de préparation (données réelles) et de cartographie (sorties simulées).

Le même tracé des deux côtés, pour que la comparaison réel / simulé se fasse à l'œil sans se demander
si c'est le graphique qui diffère. matplotlib n'est importé qu'à l'appel (dépendance de développement).
"""
from __future__ import annotations

import pandas as pd

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def entrees_par_jour(venues_entree: pd.Series, sejours_entree: pd.Series, sejours_urgences: pd.Series,
                     debut=None, fin=None) -> pd.DataFrame:
    """La table des entrées par jour : venues aux urgences, séjours entrés par les urgences, séjours programmés.

    ``venues_entree`` : date-heure d'arrivée de chaque venue ; ``sejours_entree`` : date-heure d'entrée de
    chaque séjour ; ``sejours_urgences`` : booléen aligné sur ``sejours_entree`` (séjour venu des urgences).
    Les jours sans entrée comptent zéro ; ``debut`` et ``fin`` (exclu) bornent le calendrier, sinon il va de
    la première à la dernière date vue.
    """
    venues_entree = pd.to_datetime(venues_entree)
    sejours_entree = pd.to_datetime(sejours_entree)
    sejours_urgences = pd.Series(sejours_urgences, index=sejours_entree.index).astype(bool)
    jour = lambda s: s.dt.normalize()
    t = pd.DataFrame({
        "venues aux urgences": venues_entree.groupby(jour(venues_entree)).size(),
        "séjours entrés par les urgences": sejours_entree[sejours_urgences].groupby(jour(sejours_entree[sejours_urgences])).size(),
        "séjours programmés": sejours_entree[~sejours_urgences].groupby(jour(sejours_entree[~sejours_urgences])).size(),
    })
    debut = pd.Timestamp(debut) if debut is not None else t.index.min()
    fin = pd.Timestamp(fin) if fin is not None else t.index.max() + pd.Timedelta(days=1)
    return t.reindex(pd.date_range(debut, fin - pd.Timedelta(days=1)), fill_value=0)


def tracer_entrees_par_jour(entrees: pd.DataFrame, titre: str = "Entrées par jour", afficher_semaine: bool = True):
    """Deux graphiques superposés : les venues aux urgences ; les séjours venus des urgences et programmés.
    Trait fin : la valeur du jour ; trait épais : moyenne glissante sur 7 jours. Rend aussi la moyenne par
    jour de semaine (DataFrame), affichée si ``afficher_semaine``."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    groupes = [["venues aux urgences"], ["séjours entrés par les urgences", "séjours programmés"]]
    for ax, cols in zip(axes, groupes):
        for c in cols:
            trait, = ax.plot(entrees.index, entrees[c].rolling(7, center=True).mean(), lw=2, label=c)
            ax.plot(entrees.index, entrees[c], lw=0.6, alpha=0.4, color=trait.get_color())
        ax.legend(loc="upper right")
        ax.set_ylabel("par jour")
    axes[0].set_title(titre)
    plt.tight_layout()
    plt.show()

    semaine = entrees.groupby(entrees.index.dayofweek).mean().round(1)
    semaine.index = [JOURS[i] for i in semaine.index]
    if afficher_semaine:
        print("moyenne par jour de semaine :")
        try:
            from IPython.display import display
            display(semaine.T)
        except ImportError:
            print(semaine.T.to_string())
    return semaine
