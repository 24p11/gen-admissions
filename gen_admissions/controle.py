"""Tableau de contrôle : les indicateurs du jeu de données comparés aux cibles.

Les indicateurs suivent la section 7.1 du plan d'analyse. Un jeu de données
est crédible si ses indicateurs tombent dans les fourchettes du fichier
``cibles.csv``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .moteur import Resultat


def occupation_horaire(r: Resultat) -> pd.DataFrame:
    """Nombre de lits occupés par service, heure par heure, sur la période de collecte."""
    heures = np.arange(r.chauffe_h, r.fin_collecte_h, 1.0)
    colonnes = {}
    evenements = pd.DataFrame(r.occupation, columns=["t", "service", "delta"])
    for service in r.parametres.services_avec_lits:
        ev = evenements[evenements["service"] == service].sort_values("t")
        t = ev["t"].to_numpy()
        cumul = np.cumsum(ev["delta"].to_numpy())
        idx = np.searchsorted(t, heures, side="right") - 1
        colonnes[service] = np.where(idx >= 0, cumul[np.clip(idx, 0, None)], 0)
    df = pd.DataFrame(colonnes)
    df.insert(0, "date_heure", [r.date(h) for h in heures])
    return df


def occupation_journaliere(r: Resultat) -> pd.DataFrame:
    h = occupation_horaire(r)
    h["date"] = pd.to_datetime(h["date_heure"]).dt.date
    return h.drop(columns="date_heure").groupby("date").mean().round(2).reset_index()


def indicateurs(r: Resultat, t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Calcule les indicateurs de contrôle (une ligne par indicateur et par service)."""
    p = r.parametres
    lignes = []
    occ = occupation_horaire(r)
    for s in p.services_avec_lits:
        lignes.append(("taux_occupation", s, round(float(occ[s].mean()) / p.services[s].lits, 3)))

    # Durée moyenne par spécialité : comme à l'extraction, on neutralise les
    # hébergements (spécialité prévue) et on fusionne les passages consécutifs.
    rum = t["rum"].dropna(subset=["duree_h"]).sort_values(["id_sejour", "rang"])
    bloc = (rum["specialite_prevue"] != rum.groupby("id_sejour")["specialite_prevue"].shift()).cumsum()
    etapes = rum.groupby(bloc).agg(specialite=("specialite_prevue", "first"), duree_h=("duree_h", "sum"))
    for s, grp in etapes.groupby("specialite"):
        lignes.append(("dms_j", s, round(float(grp["duree_h"].mean()) / 24.0, 2)))

    rss, rpu = t["rss"], t["rpu"]
    if len(rss):
        lignes.append(("part_sejours_urgences", "", round(float((rss["provenance"] == "5").mean()), 3)))
        lignes.append(("nb_rum_moyen", "", round(float(rss["nb_rum"].mean()), 2)))
    if len(rpu):
        lignes.append(("taux_hospitalisation_urgences", "", round(float((rpu["mode_sortie"] == "mutation").mean()), 3)))
    vs, vp = t["verite_sejours"], t["verite_passages"]
    if len(vs):
        lignes.append(("taux_hebergement", "", round(float((vs["nb_hebergements"] > 0).mean()), 3)))
    if len(vp):
        attentes = vp["attente_lit_h"].dropna()
        if len(attentes):
            lignes.append(("attente_mediane_urgences_h", "", round(float(attentes.median()), 2)))
            lignes.append(("attente_moyenne_urgences_h", "", round(float(attentes.mean()), 2)))
        voulaient_un_lit = vp["transfert_faute_de_lit"].sum() + (rpu["mode_sortie"] == "mutation").sum()
        if voulaient_un_lit:
            lignes.append(("taux_transfert_faute_de_lit", "", round(float(vp["transfert_faute_de_lit"].sum() / voulaient_un_lit), 4)))
    # réhospitalisation à 30 jours
    if len(rss):
        d = rss[rss["date_sortie"] != ""].copy()
        d["sortie"] = pd.to_datetime(d["date_sortie"])
        d["entree"] = pd.to_datetime(d["date_entree"])
        d = d.sort_values(["id_patient", "entree"])
        d["entree_suivante"] = d.groupby("id_patient")["entree"].shift(-1)
        delai = (d["entree_suivante"] - d["sortie"]).dt.days
        lignes.append(("taux_rehospitalisation_30j", "", round(float(((delai >= 0) & (delai <= 30)).mean()), 3)))

    df = pd.DataFrame(lignes, columns=["indicateur", "service", "valeur"])
    cibles = p.cibles.copy()
    if len(cibles):
        cibles["valeur_min"] = cibles["valeur_min"].astype(float)
        cibles["valeur_max"] = cibles["valeur_max"].astype(float)
        df = df.merge(cibles, on=["indicateur", "service"], how="left")
        df["statut"] = np.where(df["valeur_min"].isna(), "",
                                np.where((df["valeur"] >= df["valeur_min"]) & (df["valeur"] <= df["valeur_max"]), "OK", "HORS CIBLE"))
    return df


def comparaison_trajectoires(r: Resultat, t: dict[str, pd.DataFrame], n: int = 20) -> pd.DataFrame:
    """Part des trajectoires les plus fréquentes : catalogue (attendu) contre simulation (réalisé)."""
    cat = r.parametres.catalogue.groupby("trajectoire")["effectif"].sum()
    attendu = (cat / cat.sum()).rename("part_catalogue")
    vs = t["verite_sejours"]
    # la trajectoire « médicale » réalisée : on retire les hébergements (spécialité prévue) et on fusionne
    rum = t["rum"].sort_values(["id_sejour", "rang"])
    realisees = rum.groupby("id_sejour")["specialite_prevue"].agg(
        lambda s: " > ".join(x for i, x in enumerate(s) if i == 0 or x != s.iloc[i - 1]))
    simule = realisees.value_counts(normalize=True).rename("part_simulee")
    df = pd.concat([attendu, simule], axis=1).fillna(0.0)
    df["ecart"] = df["part_simulee"] - df["part_catalogue"]
    return df.sort_values("part_catalogue", ascending=False).head(n).round(4).reset_index(names="trajectoire")


def rapport(r: Resultat, t: dict[str, pd.DataFrame], dossier: str | Path) -> pd.DataFrame:
    """Écrit le tableau de contrôle (CSV + Markdown), l'occupation journalière et un graphique."""
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    ind = indicateurs(r, t)
    ind.to_csv(dossier / "controle.csv", index=False)
    occ = occupation_journaliere(r)
    occ.to_csv(dossier / "occupation_journaliere.csv", index=False)
    traj = comparaison_trajectoires(r, t)
    traj.to_csv(dossier / "comparaison_trajectoires.csv", index=False)

    lignes = ["# Tableau de contrôle", "", f"Jeu de données fictif — graine {r.graine} — collecte du {r.date(r.chauffe_h):%Y-%m-%d} au {r.date(r.fin_collecte_h):%Y-%m-%d}", "",
              "| Indicateur | Service | Valeur | Cible min | Cible max | Statut |", "| --- | --- | --- | --- | --- | --- |"]
    for _, x in ind.iterrows():
        vmin = "" if pd.isna(x.get("valeur_min", np.nan)) else x["valeur_min"]
        vmax = "" if pd.isna(x.get("valeur_max", np.nan)) else x["valeur_max"]
        lignes.append(f"| {x['indicateur']} | {x['service']} | {x['valeur']} | {vmin} | {vmax} | {x.get('statut', '')} |")
    lignes += ["", "## Trajectoires les plus fréquentes : catalogue contre simulation", "",
               "| Trajectoire | Part catalogue | Part simulée | Écart |", "| --- | --- | --- | --- |"]
    for _, x in traj.iterrows():
        lignes.append(f"| {x['trajectoire']} | {x['part_catalogue']:.3f} | {x['part_simulee']:.3f} | {x['ecart']:+.3f} |")
    (dossier / "controle.md").write_text("\n".join(lignes) + "\n", encoding="utf-8")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        services = r.parametres.services_avec_lits
        n = len(services)
        cols = 4
        rows = int(np.ceil(n / cols))
        fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 2.4 * rows), sharex=True)
        for ax, s in zip(axes.flat, services):
            lits = r.parametres.services[s].lits
            ax.plot(pd.to_datetime(occ["date"]), occ[s], lw=0.8, color="#1F3A68")
            ax.axhline(lits, color="#C55A11", lw=0.8, ls="--")
            ax.set_title(f"{s} ({lits} lits)", fontsize=9)
            ax.set_ylim(0, lits * 1.1)
            ax.tick_params(labelsize=7)
        for ax in list(axes.flat)[n:]:
            ax.axis("off")
        fig.suptitle("Lits occupés par jour (moyenne journalière) — pointillés : capacité", fontsize=10)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(dossier / "occupation.png", dpi=110)
        plt.close(fig)
    except Exception as e:  # pragma: no cover - le graphique est un bonus
        (dossier / "occupation_graphique_erreur.txt").write_text(str(e))
    return ind
