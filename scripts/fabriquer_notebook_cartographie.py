"""Fabrique notebooks/cartographie_hopital.ipynb : la cartographie d'un hôpital à
partir de ses 22 fichiers de paramètres (structure, volumes déduits, urgences,
casemix, parcours). Le notebook est paramétré par un dossier ; il tourne sur
l'hôpital fictif comme sur un dossier réel.

Usage : python scripts/fabriquer_notebook_cartographie.py
"""
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

SORTIE = Path(__file__).resolve().parents[1] / "notebooks" / "cartographie_hopital.ipynb"

CELLULES = []
md, code = (lambda s: CELLULES.append(new_markdown_cell(s.strip("\n")))), (lambda s: CELLULES.append(new_code_cell(s.strip("\n"))))

md("""
# Cartographie d'un hôpital à partir de ses paramètres

Ce notebook lit les 22 fichiers de paramètres d'un hôpital (le format décrit dans
`parametres/DICTIONNAIRE.md`) et en tire une vue d'ensemble de son activité : sa
structure, les volumes qu'impliquent les paramètres, le fonctionnement des urgences,
le casemix, les parcours. Il sert à deux choses :

- **comprendre l'hôpital** que l'on va simuler, et vérifier que les paramètres se
  tiennent entre eux (les volumes déduits par deux chemins indépendants doivent se
  recouper) ;
- plus tard, **comparer le simulé au réel** : les mêmes graphiques, tracés sur la
  sortie du simulateur, doivent ressembler à ceux-ci.

Les fichiers ne contiennent que des proportions. Les nombres absolus (séjours par an,
hospitalisations depuis les urgences) sont donc *déduits*, par le même raisonnement
que le simulateur emploiera (section 2).
""")

code("""
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
import matplotlib.pyplot as plt
import matplotlib as mpl

# le dépôt : on remonte jusqu'au dossier qui contient gen_admissions/, pour trouver le module
RACINE = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "gen_admissions").exists())
import sys; sys.path.insert(0, str(RACINE))
from gen_admissions.preparation_parametres import restreindre_services

plt.rcParams.update({"figure.figsize": (10, 4.5), "axes.grid": True, "grid.alpha": 0.3, "axes.spines.top": False, "axes.spines.right": False})
CLASSES_AGE = ["0-17", "18-64", "65-74", "75+"]
TRANCHES_H = ["0-1", "1-2", "2-4", "4-6", "6-12", "12-24", "24+"]
JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
""")

md("""
## Réglages

`DOSSIER` est le dossier des 22 fichiers : l'hôpital fictif du dépôt par défaut ; pour un
hôpital réel, le dossier où le notebook de préparation a écrit ses fichiers (`prod/`, qui
n'est pas suivi par git). `FICHIER_TENSION` est facultatif : la série quotidienne de la
tension n'est pas dans les 22 fichiers (seulement ses bornes) ; si un fichier parquet avec
les colonnes `date` et `tension` est là, le notebook trace la série, sinon il passe.
""")

code("""
DOSSIER = RACINE / "parametres" / "hopital_type"      # chez soi : Path("prod") ou un chemin absolu
FICHIER_TENSION = DOSSIER / "tension.parquet"        # facultatif ; dans le notebook de préparation :
                                                     # tension_ref[tension_ref.nohop == NOHOP].to_parquet(DOSSIER_SORTIE / "tension.parquet", index=False)
""")

md("""
## Lecture des fichiers

Tout est lu en texte, puis les colonnes numériques sont converties : ainsi les cases vides
des colonnes de bloc (les lignes de repli) restent des chaînes vides et non des `NaN`, ce
qui simplifie les filtres (`t[t.classe_age != ""]` sélectionne le niveau fin).
""")

code("""
def lire(nom, nombres=("part",)):
    t = pd.read_csv(DOSSIER / nom, dtype=str, keep_default_na=False)
    for c in nombres:
        if c in t.columns:
            t[c] = pd.to_numeric(t[c].replace("", np.nan))
    return t

P = {
    "structure": yaml.safe_load(open(DOSSIER / "structure.yaml", encoding="utf-8")),
    "specialites": lire("specialites.csv"),
    "regroupements": lire("regroupements_ghm.csv"),
    "niveaux": lire("niveaux.csv"),
    "durees_ghm": lire("durees_ghm.csv", ("part", "moyenne_60plus")),
    "soins_critiques": lire("soins_critiques_ghm.csv", ("part_rea", "part_usi", "part_usc")),
    "sexe_racine": lire("sexe_racine.csv"),
    "sorties": lire("sorties.csv"),
    "premier_service": lire("premier_service_racine.csv", ("part", "rang")),
    "casemix": lire("casemix.csv"),
    "table_passage": lire("table_passage.csv", ("part", "rang")),
    "durees_passage": lire("durees_passage.csv", ("mediane_j", "q1_j", "q3_j", "moyenne_j")),
    "venues": lire("venues_urgences.csv"),
    "arrivees": lire("profil_arrivees_urgences.csv"),
    "patients": lire("profil_patients_urgences.csv"),
    "gravite": lire("gravite_urgences.csv"),
    "classes_tension": lire("classes_tension.csv", ("borne_inf", "borne_sup")),
    "durees_urgences": lire("durees_passage_urgences.csv"),
    "supplement": lire("supplement_rang2.csv"),
    "entrees_programmees": lire("profil_entrees_programmees.csv"),
    "profil_sorties": lire("profil_sorties.csv"),
    "cibles": lire("cibles.csv", ("observe", "min", "max")),
}
S = P["structure"]
SAISON = S["urgences"]["saison"]                       # la saison de référence des tables saisonnières
print(S["etablissement"], "—", S["description"])
print("saison de référence :", SAISON, "; passages aux urgences par an :", S["urgences"]["passages_par_an"])
""")

md("""
## 1. La structure

Les services, leurs lits et les **présents visés** (le nombre moyen de patients que le
simulateur devra maintenir dans chaque service, mesuré sur les données). Le rapport
présents ÷ lits est une occupation *implicite* : si elle dépasse 1, le service accueille
en moyenne plus de patients qu'il n'a de lits déclarés — les lits déclarés ne sont pas les
lits réellement ouverts, c'est précisément pourquoi le modèle travaille en présents et non
en taux d'occupation. Les services de soins critiques et les urgences n'ont pas de
présents visés : leur occupation résulte entièrement des flux.
""")

code("""
services = pd.DataFrame([{"service": k, **v} for k, v in S["services"].items()])
services = services.merge(P["specialites"][["specialite", "type"]], on="specialite", how="left")
services["soins_critiques"] = services.get("soins_critiques", pd.Series([None] * len(services))).apply(lambda x: ",".join(x) if isinstance(x, list) else "")
services["occupation_implicite"] = (services["presents_vises"] / services["lits"]).round(2)
colonnes = [c for c in ["service", "libelle", "type", "lits", "presents_vises", "occupation_implicite", "soins_critiques", "age_min", "age_max", "sexe"] if c in services.columns]
print(f"{len(services)} services, {int(services['lits'].sum())} lits déclarés, "
      f"{services['presents_vises'].sum():.0f} présents visés dans les services conventionnels")
services[colonnes]
""")

code("""
conv = services[services["presents_vises"].notna()].sort_values("lits", ascending=False)
fig, ax = plt.subplots(figsize=(10, 0.4 * len(conv) + 1))
y = np.arange(len(conv))
ax.barh(y + 0.2, conv["lits"], height=0.4, label="lits déclarés", color="#c8c8c8")
ax.barh(y - 0.2, conv["presents_vises"], height=0.4, label="présents visés (moyenne observée)", color="#2a6f97")
ax.set_yticks(y); ax.set_yticklabels(conv["service"]); ax.invert_yaxis()
ax.set_xlabel("patients"); ax.set_title("Lits déclarés et présents moyens par service conventionnel"); ax.legend(loc="lower right")
plt.show()
""")

md("""
## 2. Les volumes que les paramètres impliquent

Le simulateur ne connaît pas le nombre de séjours : il le déduit de la **loi de Little**,
qui relie trois grandeurs d'un système stable : *présents = entrées par jour × durée moyenne
de passage*. Pour chaque service, les présents visés viennent de la structure et la durée
moyenne de `durees_passage.csv` (ligne de repli, toutes entrées confondues) ; on en tire les
entrées par an, puis la part venue des urgences (indicateur `part_urgences` des cibles) les
répartit entre urgences et programmé.

Un **second chemin**, indépendant, donne le nombre d'hospitalisations depuis les urgences :
passages par an × part des venues qui finissent hospitalisées, cette part étant recomposée
depuis `venues_urgences.csv` en pondérant chaque case (mois × classe d'âge) par son poids
dans les arrivées. Les deux chemins doivent donner des nombres voisins — la différence tient
aux mutations (un séjour venu des urgences entre dans plusieurs services successifs, donc
compte plusieurs entrées de service pour une seule hospitalisation) et au bruit des
estimations. Un écart très grand signalerait des paramètres incohérents.
""")

code("""
# chemin 1 : loi de Little, service par service
dp = P["durees_passage"]
moy = dp[(dp["mode_entree"] == "") & (dp["classe_age"] == "")].set_index("specialite")["moyenne_j"]
part_urg = P["cibles"].query("indicateur == 'part_urgences'").set_index("service")["observe"]
vol = conv.set_index("service")[["lits", "presents_vises"]].copy()
vol["duree_moyenne_j"] = moy.reindex(vol.index)
vol["entrees_par_an"] = (vol["presents_vises"] * 365 / vol["duree_moyenne_j"]).round()
vol["part_urgences"] = part_urg.reindex(vol.index)
vol["dont_urgences"] = (vol["entrees_par_an"] * vol["part_urgences"]).round()
vol["dont_programme"] = vol["entrees_par_an"] - vol["dont_urgences"]
total = vol[["entrees_par_an", "dont_urgences", "dont_programme"]].sum()
print(f"Entrées de service par an (loi de Little) : {total['entrees_par_an']:.0f}, "
      f"dont {total['dont_urgences']:.0f} venues des urgences et {total['dont_programme']:.0f} programmées")
vol.round(2)
""")

code("""
# chemin 2 : passages aux urgences × part d'hospitalisation, recomposée case par case
arr = P["arrivees"]; pat = P["patients"]
def entier_texte(x):
    # « 1.0 », « 1 » ou 1 → « 1 » (les fichiers plus anciens écrivaient des flottants)
    return pd.to_numeric(x).astype(int).astype(str)
poids_mois = arr[(arr["saison"] == SAISON) & (arr["dimension"] == "mois")].set_index("mois")["part"]
poids_mois.index = entier_texte(poids_mois.index)
v = P["venues"].copy(); v.loc[v["periode"] != "", "periode"] = entier_texte(v.loc[v["periode"] != "", "periode"])
poids_age = pat.groupby("classe_age")["part"].sum()
v = v[(v["saison"] == SAISON) & (v["periode"] != "")]
w = v["periode"].map(poids_mois) * v["classe_age"].map(poids_age)
situations = (v.assign(w=w).groupby(["periode", "classe_age"]).apply(lambda g: g.set_index("situation")["part"].groupby(level=0).sum() * g["w"].iloc[0]).groupby(level=-1).sum())
situations = situations / situations.sum()
passages = S["urgences"]["passages_par_an"]
print("Devenir des venues (saison", SAISON, ") :")
print((situations * 100).round(1).astype(str) + " %")
hosp_venues = passages * situations.get("hospitalisation", 0)
print(f"\\nHospitalisations depuis les urgences : {hosp_venues:.0f} par an (chemin des venues) "
      f"contre {total['dont_urgences']:.0f} entrées de service venues des urgences (loi de Little)")
print(f"Rapport entrées de service / hospitalisations = {total['dont_urgences'] / hosp_venues:.2f} "
      "(> 1 attendu : un séjour peut passer par plusieurs services)")
print(f"Séjours limités à l'UHCD : {passages * situations.get('mono_uhcd', 0):.0f} par an")
""")

code("""
fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
vv = vol.sort_values("entrees_par_an", ascending=False)
axes[0].bar(vv.index, vv["dont_urgences"], label="venues des urgences", color="#d1495b")
axes[0].bar(vv.index, vv["dont_programme"], bottom=vv["dont_urgences"], label="programmées", color="#2a6f97")
axes[0].set_title("Entrées de service par an (déduites)"); axes[0].tick_params(axis="x", rotation=90); axes[0].legend()
axes[1].pie(situations.values, labels=[f"{k} ({v:.1%})" for k, v in situations.items()], startangle=90, counterclock=False, colors=plt.cm.Set2.colors)
axes[1].set_title(f"Devenir des {passages} passages annuels aux urgences")
plt.tight_layout(); plt.show()
""")

md("""
## 3. Les urgences

### Quand arrivent les patients

La carte jour × heure (168 cases) est la dimension `semaine` du profil d'arrivées ; la courbe
mensuelle est sa dimension `mois`, une courbe par saison (septembre à août). Les deux
dimensions sont indépendantes : le simulateur combine le rythme hebdomadaire et la
saisonnalité.
""")

code("""
sem = arr[(arr["saison"] == SAISON) & (arr["dimension"] == "semaine")].copy()
sem["jour_semaine"] = pd.to_numeric(sem["jour_semaine"]).astype(int); sem["heure"] = pd.to_numeric(sem["heure"]).astype(int)
carte = sem.pivot(index="jour_semaine", columns="heure", values="part").reindex(index=range(1, 8), columns=range(24)).fillna(0)
carte = carte * passages          # en passages par an par case
fig, axes = plt.subplots(1, 2, figsize=(14, 4), gridspec_kw={"width_ratios": [3, 2]})
im = axes[0].imshow(carte.values, aspect="auto", cmap="YlOrRd")
axes[0].set_yticks(range(7)); axes[0].set_yticklabels(JOURS); axes[0].set_xticks(range(0, 24, 2)); axes[0].set_xlabel("heure d'arrivée")
axes[0].set_title(f"Arrivées par jour de semaine et heure (saison {SAISON}, passages/an par case)"); axes[0].grid(False)
plt.colorbar(im, ax=axes[0], fraction=0.03)
mois = arr[arr["dimension"] == "mois"].copy(); mois["mois"] = pd.to_numeric(mois["mois"]).astype(int)
ordre = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8]
for saison_, g in mois.groupby("saison"):
    serie = g.set_index("mois")["part"].reindex(ordre)
    axes[1].plot(range(12), serie.values * 12, marker="o", label=saison_)
axes[1].axhline(1, color="grey", lw=0.8); axes[1].set_xticks(range(12)); axes[1].set_xticklabels(ordre)
axes[1].set_title("Saisonnalité (1 = mois moyen)"); axes[1].set_xlabel("mois, de septembre à août"); axes[1].legend()
plt.tight_layout(); plt.show()
""")

md("""
### Qui arrive, et ce qui leur arrive

La pyramide âge × sexe vient de `profil_patients_urgences.csv`. Les situations par classe
d'âge sont recomposées depuis `venues_urgences.csv` (moyenne sur les mois, pondérée par les
arrivées) : c'est là qu'on lit le gradient d'âge de l'hospitalisation. La gravité CCMU des
hospitalisés (`gravite_urgences.csv`) est tirée *après* la durée de passage ; on la regarde
ici par situation.
""")

code("""
fig, axes = plt.subplots(1, 3, figsize=(15, 4))
pyr = pat.pivot(index="classe_age", columns="sexe", values="part").reindex(CLASSES_AGE).fillna(0)
axes[0].barh(CLASSES_AGE, -pyr.get("M", 0), color="#2a6f97", label="hommes"); axes[0].barh(CLASSES_AGE, pyr.get("F", 0), color="#d1495b", label="femmes")
axes[0].set_xticks(axes[0].get_xticks()); axes[0].set_xticklabels([f"{abs(x):.0%}" for x in axes[0].get_xticks()])
axes[0].set_title("Patients des urgences : âge et sexe"); axes[0].legend()

sit_age = v.assign(w=w).groupby(["classe_age", "situation"]).apply(lambda g: (g["part"] * g["w"]).sum() / g["w"].sum() if g["w"].sum() else 0).unstack(fill_value=0)
sit_age = sit_age.div(sit_age.sum(axis=1), axis=0).reindex(CLASSES_AGE)
sit_age[["consultation", "mono_uhcd", "hospitalisation", "transfert", "deces"]].plot.bar(stacked=True, ax=axes[1], color=plt.cm.Set2.colors, rot=0)
axes[1].set_title("Devenir de la venue selon l'âge"); axes[1].set_ylabel("part des venues"); axes[1].legend(fontsize=8)

gr = P["gravite"]; gr = gr[gr["tranche_h"] == ""]       # ligne de repli : par classe d'âge × situation, toutes durées
g_age = gr.groupby(["classe_age", "gravite"])["part"].mean().unstack(fill_value=0).reindex(CLASSES_AGE)
g_age = g_age.div(g_age.sum(axis=1), axis=0)
g_age.plot.bar(stacked=True, ax=axes[2], colormap="Reds", rot=0)
axes[2].set_title("CCMU des hospitalisés selon l'âge"); axes[2].legend(title="CCMU", fontsize=8)
plt.tight_layout(); plt.show()
""")

md("""
### Combien de temps aux urgences, et le rôle de la tension

La tension du jour est le nombre de patients présents dans l'hôpital venus des urgences, au
relevé du matin ; ses bornes (terciles) définissent trois classes. Le tableau montre, pour les
hospitalisés de 75 ans et plus, la répartition de la durée de passage par classe de tension :
c'est là que la tension doit se voir (la masse glisse vers les tranches longues). Les courbes
donnent la tranche médiane par situation. Le supplément de rang 2 est l'attente supplémentaire
des patients qui partent dans un service exceptionnel, par classe de tension.
""")

code("""
ct = P["classes_tension"]; print("Bornes de la tension (présents venus des urgences) :"); print(ct.to_string(index=False))
du = P["durees_urgences"]
fin = du[(du["classe_tension"] != "")]
tab = fin[(fin["situation"] == "hospitalisation") & (fin["classe_age"] == "75+")].pivot_table(index="tranche_h", columns="classe_tension", values="part", aggfunc="sum").reindex(TRANCHES_H)
tab = tab[[c for c in ["basse", "moyenne", "haute"] if c in tab.columns]]
print("\\nHospitalisés de 75 ans et plus : part des venues par tranche de durée et classe de tension")
print((tab * 100).round(1).to_string())

def tranche_mediane(g):
    cum = g.set_index("tranche_h")["part"].reindex(TRANCHES_H).fillna(0).cumsum()
    return cum[cum >= 0.5].index[0] if (cum >= 0.5).any() else ""
fig, axes = plt.subplots(1, 2, figsize=(13, 4))
repli = du[(du["classe_tension"] == "") & (du["gravite"] == "")]       # repli situation × âge, toutes gravités et tensions
for sit, g in repli.groupby("situation"):
    med = g.groupby("classe_age").apply(tranche_mediane).reindex(CLASSES_AGE)
    axes[0].plot(CLASSES_AGE, [TRANCHES_H.index(m) if m in TRANCHES_H else np.nan for m in med], marker="o", label=sit)
axes[0].set_yticks(range(len(TRANCHES_H))); axes[0].set_yticklabels(TRANCHES_H); axes[0].set_ylabel("tranche médiane de durée (h)")
axes[0].set_title("Durée de passage médiane par situation et âge"); axes[0].legend(fontsize=8)
sup = P["supplement"].pivot_table(index="tranche_h", columns="classe_tension", values="part", aggfunc="sum").reindex(TRANCHES_H).fillna(0)
sup = sup[[c for c in ["basse", "moyenne", "haute"] if c in sup.columns]]
sup.plot.bar(ax=axes[1], rot=0, color=["#8ecae6", "#219ebc", "#023047"])
axes[1].set_title("Supplément d'attente avant un service de rang 2"); axes[1].set_xlabel("heures supplémentaires"); axes[1].set_ylabel("part des venues de rang 2")
plt.tight_layout(); plt.show()
""")

code("""
if FICHIER_TENSION.exists():
    tension = pd.read_parquet(FICHIER_TENSION); tension["date"] = pd.to_datetime(tension["date"])
    if "nohop" in tension.columns and tension["nohop"].nunique() > 1:
        tension = tension[tension["nohop"] == tension["nohop"].iloc[0]]
    fig, ax = plt.subplots(figsize=(13, 4))
    ax.plot(tension["date"], tension["tension"], lw=0.8, color="#2a6f97")
    ax.plot(tension["date"], tension["tension"].rolling(7, center=True).mean(), lw=2, color="#d1495b", label="moyenne glissante 7 jours")
    for _, b in ct.iterrows():
        if pd.notna(b["borne_sup"]):
            ax.axhline(b["borne_sup"], color="grey", ls="--", lw=0.8)
    ax.set_title("Tension quotidienne : patients présents venus des urgences (tirets : bornes des classes)"); ax.legend()
    plt.show()
else:
    print("Pas de série quotidienne de tension (fichier", FICHIER_TENSION.name, "absent) : seules les bornes sont connues.")
""")

md("""
## 4. Le casemix

### Ce qui entre par les urgences

`venues_urgences.csv` donne, pour les venues hospitalisées, la racine de GHM. En pondérant
les cases par les arrivées, on obtient la distribution des racines des hospitalisations
venues des urgences — le casemix « non programmé » de l'hôpital. Les libellés viennent de
`regroupements_ghm.csv`, qui permet aussi de regrouper par domaine d'activité (`da`).
""")

code("""
rgp = P["regroupements"]
libelle_racine = rgp.groupby("racine_ghm")["libelle"].first().str.replace(r",? niveau \\d$", "", regex=True)
da_racine = rgp.groupby("racine_ghm")["da"].first()
h = v[v["situation"] == "hospitalisation"].assign(w=w)
racines_urg = (h["part"] * h["w"]).groupby(h["racine_ghm"]).sum()
racines_urg = racines_urg / racines_urg.sum()
print(f"{len(racines_urg)} racines distinctes parmi les hospitalisations venues des urgences ; "
      f"{len(rgp['racine_ghm'].unique())} racines et {len(rgp)} GHM dans la nomenclature")
top = racines_urg.sort_values(ascending=False).head(20)
fig, axes = plt.subplots(1, 2, figsize=(15, 6), gridspec_kw={"width_ratios": [3, 2]})
axes[0].barh([f"{r} {libelle_racine.get(r, '')[:45]}" for r in top.index][::-1], top.values[::-1], color="#d1495b")
axes[0].set_xlabel("part des hospitalisations venues des urgences"); axes[0].set_title("Les 20 racines les plus fréquentes")
par_da = racines_urg.groupby(racines_urg.index.map(da_racine).fillna("?")).sum().sort_values()
axes[1].barh(par_da.index, par_da.values, color="#2a6f97"); axes[1].set_title("Par domaine d'activité (da)")
plt.tight_layout(); plt.show()
""")

md("""
### Ce qui entre en programmé

`casemix.csv` décrit, pour chaque spécialité, les racines et classes d'âge de ses entrées
programmées. On en tire, pour chaque service, ses trois racines principales et la part des
75 ans et plus.
""")

code("""
cm = P["casemix"]
lignes = []
for spec, g in cm.groupby("specialite"):
    par_racine = g.groupby("racine_ghm")["part"].sum().sort_values(ascending=False)
    lignes.append({"specialite": spec, "nb_racines": len(par_racine),
                   "racines_principales": " ; ".join(f"{r} {libelle_racine.get(r, '')[:30]} ({p:.0%})" for r, p in par_racine.head(3).items()),
                   "part_75+": round(g.loc[g["classe_age"] == "75+", "part"].sum(), 2),
                   "part_0-17": round(g.loc[g["classe_age"] == "0-17", "part"].sum(), 2)})
pd.set_option("display.max_colwidth", 120)
pd.DataFrame(lignes).sort_values("specialite").reset_index(drop=True)
""")

md("""
### Sévérité, durées et sorties

Trois tables par GHM, lues sur les racines les plus fréquentes aux urgences : la part des
niveaux de sévérité selon la classe d'âge (`niveaux.csv`, pondérée par la fréquence des
racines), la distribution des durées des séjours pour six racines (`durees_ghm.csv`, ligne
de repli par racine), et les modes de sortie par racine (`sorties.csv`, repli racine).
""")

code("""
niv = P["niveaux"]; niv = niv[niv["classe_age"] != ""]
niv = niv.assign(w=niv["racine_ghm"].map(racines_urg).fillna(0))
niv_age = (niv["part"] * niv["w"]).groupby([niv["classe_age"], niv["niveau"]]).sum().unstack(fill_value=0).reindex(CLASSES_AGE)
niv_age = niv_age.div(niv_age.sum(axis=1), axis=0)
dg = P["durees_ghm"]; dg = dg[dg["classe_age_duree"] == ""]            # repli : par GHM ou racine, tous âges
six = top.index[:6]
so = P["sorties"]; so = so[(so["niveau"] == "") & (so["classe_age"] == "")]
sorties_top = so[so["racine_ghm"].isin(top.index[:12])].pivot_table(index="racine_ghm", columns="mode_sortie", values="part", aggfunc="sum").reindex(top.index[:12]).fillna(0)

fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
niv_age.plot.bar(stacked=True, ax=axes[0], colormap="Blues", rot=0); axes[0].set_title("Niveau de sévérité selon l'âge\\n(racines pondérées par leur fréquence aux urgences)"); axes[0].legend(title="niveau", fontsize=8)
for r in six:
    d = dg[dg["ghm"] == r].copy()
    if d.empty: continue
    d["j"] = pd.to_numeric(d["duree_j"].replace("60+", "61")); d = d.sort_values("j")
    axes[1].plot(d["j"], d["part"].cumsum(), label=f"{r} {libelle_racine.get(r, '')[:20]}")
axes[1].set_xlim(0, 30); axes[1].set_xlabel("durée du séjour (jours)"); axes[1].set_ylabel("part cumulée"); axes[1].set_title("Durées de séjour, six racines fréquentes"); axes[1].legend(fontsize=7)
sorties_top.plot.barh(stacked=True, ax=axes[2], color=plt.cm.Set2.colors); axes[2].invert_yaxis(); axes[2].set_title("Mode de sortie des 12 racines les plus fréquentes"); axes[2].legend(fontsize=7)
plt.tight_layout(); plt.show()
""")

md("""
## 5. Les parcours

### Le premier service d'hospitalisation

`premier_service_racine.csv` est calculé sur plusieurs établissements ; comme le fera le
simulateur, on le **restreint aux services présents dans la structure**, on renormalise et
on recalcule les rangs. La carte montre, pour les 25 racines les plus fréquentes aux
urgences, la part de chaque service d'accueil (toutes classes d'âge : ligne de repli), un
point blanc marquant les services de rang 2. Une racine sans aucun service dans l'hôpital
(ligne vide) conduit au transfert.
""")

code("""
ps = P["premier_service"]
ps_restreint = restreindre_services(ps, services["service"], ["racine_ghm", "classe_age"])
# une racine dont il ne reste que des services de soins critiques n'a pas non plus de service d'accueil :
# la réanimation reçoit des patients qui ont une spécialité derrière eux, elle ne la remplace pas
conventionnels = set(services.loc[~services["type"].isin(["soins_critiques", "urgences"]), "service"])
avec_accueil = set(ps_restreint.loc[ps_restreint["specialite"].isin(conventionnels), "racine_ghm"])
perdues = set(ps["racine_ghm"]) - avec_accueil
print(f"{ps['specialite'].nunique()} spécialités dans la table multi-sites, {ps_restreint['specialite'].nunique()} présentes dans l'hôpital ; "
      f"{len(perdues)} racines sans service d'accueil conventionnel (→ transfert)" + (f" : {sorted(perdues)[:10]}" if perdues else ""))
r25 = top.index[:25]
rep = ps_restreint[(ps_restreint["classe_age"] == "") & ps_restreint["racine_ghm"].isin(r25)]
carte = rep.pivot_table(index="racine_ghm", columns="specialite", values="part", aggfunc="sum").reindex(r25).fillna(0)
rangs = rep.pivot_table(index="racine_ghm", columns="specialite", values="rang", aggfunc="min").reindex(r25)
carte = carte[carte.sum().sort_values(ascending=False).index]
fig, ax = plt.subplots(figsize=(0.55 * len(carte.columns) + 4, 0.4 * len(carte) + 1))
im = ax.imshow(carte.values, cmap="Blues", aspect="auto", vmin=0, vmax=1)
for i, r in enumerate(carte.index):
    for j, sp in enumerate(carte.columns):
        if pd.notna(rangs.loc[r, sp]) and rangs.loc[r, sp] == 2:
            ax.plot(j, i, "o", color="white", ms=4)
ax.set_xticks(range(len(carte.columns))); ax.set_xticklabels(carte.columns, rotation=90)
ax.set_yticks(range(len(carte))); ax.set_yticklabels([f"{r} {libelle_racine.get(r, '')[:35]}" for r in carte.index]); ax.grid(False)
ax.set_title("Premier service par racine, après restriction à la structure (point blanc : rang 2)"); plt.colorbar(im, ax=ax, fraction=0.02)
plt.show()
""")

md("""
### La suite du parcours

`table_passage.csv`, niveau `service` (le repli : la suite ne dépend que du service en cours),
après la même restriction : pour chaque service, la part des passages qui se terminent par la
sortie de l'hôpital et la répartition des mutations vers les autres services. Puis, de
`durees_passage.csv`, la durée médiane et l'écart interquartile d'un passage par spécialité,
selon le mode d'entrée.
""")

code("""
tp = P["table_passage"]
niveau_service = tp[tp["niveau"] == "service"].copy()
suites = set(services["service"]) | {"sortie"}
niveau_service = niveau_service[niveau_service["suite"].isin(suites) & niveau_service["service"].isin(services["service"])]
niveau_service["part"] = niveau_service["part"] / niveau_service.groupby("service")["part"].transform("sum")
mat = niveau_service.pivot_table(index="service", columns="suite", values="part", aggfunc="sum").fillna(0)
mat = mat.reindex(sorted(mat.index))
colonnes = ["sortie"] + sorted(c for c in mat.columns if c != "sortie")
mat = mat.reindex(columns=colonnes, fill_value=0)
print("Part des passages qui se terminent par la sortie de l'hôpital (le reste : mutation vers un autre service) :")
print((mat["sortie"] * 100).round(0).astype(int).astype(str).add(" %").to_string())
fig, ax = plt.subplots(figsize=(0.5 * len(colonnes) + 3, 0.4 * len(mat) + 1))
im = ax.imshow(mat.values, cmap="Purples", aspect="auto", vmin=0, vmax=1)
ax.set_xticks(range(len(colonnes))); ax.set_xticklabels(colonnes, rotation=90); ax.set_yticks(range(len(mat))); ax.set_yticklabels(mat.index); ax.grid(False)
ax.set_xlabel("suite du parcours"); ax.set_ylabel("service en cours"); ax.set_title("Table de passage (niveau service), après restriction")
plt.colorbar(im, ax=ax, fraction=0.02); plt.show()
""")

code("""
d = dp[(dp["classe_age"] == "") & (dp["mode_entree"] != "")]          # repli spécialité × mode d'entrée
ordre = d.groupby("specialite")["mediane_j"].max().sort_values(ascending=False).index
fig, ax = plt.subplots(figsize=(12, 4.5))
for k, (mode, couleur) in enumerate([("urgences", "#d1495b"), ("programme", "#2a6f97")]):
    g = d[d["mode_entree"] == mode].set_index("specialite").reindex(ordre)
    x = np.arange(len(ordre)) + (k - 0.5) * 0.3
    ax.errorbar(x, g["mediane_j"], yerr=[g["mediane_j"] - g["q1_j"], g["q3_j"] - g["mediane_j"]], fmt="o", color=couleur, capsize=3, label=mode)
ax.set_xticks(range(len(ordre))); ax.set_xticklabels(ordre, rotation=90); ax.set_ylabel("jours")
ax.set_title("Durée d'un passage par spécialité : médiane et quartiles, selon le mode d'entrée"); ax.legend()
plt.tight_layout(); plt.show()
""")

md("""
## Pour finir : ce que le simulateur devra reproduire

Les cibles du tableau de contrôle (`cibles.csv`) résument ce que les données disent de
l'hôpital. Le tableau ci-dessous les liste pour l'hôpital entier ; les cibles par service
(présents moyens, durée moyenne, part urgences) ont servi aux sections 1 et 2.
""")

code("""
c = P["cibles"]
c[c["service"] == ""][["indicateur", "classe_tension", "observe", "min", "max"]].reset_index(drop=True)
""")


def main() -> None:
    nb = new_notebook(cells=CELLULES, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                                                  "language_info": {"name": "python"}})
    nbformat.write(nb, SORTIE)
    print(len(nb.cells), "cellules ->", SORTIE)


if __name__ == "__main__":
    main()
