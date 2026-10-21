"""Premiers objets métier de Cavissimo, indépendants du site web."""

from datetime import date
from math import isfinite
import re

from werkzeug.security import check_password_hash, generate_password_hash


class Utilisateur:
    """Un compte : le mot de passe en clair n'est jamais conservé."""

    def __init__(self, identifiant: str, email: str, mot_de_passe_hash: str, id=None):
        identifiant = identifiant.strip()
        email = email.strip()
        if not 3 <= len(identifiant) <= 40 or any(ord(c) < 32 for c in identifiant):
            raise ValueError("L'identifiant doit contenir entre 3 et 40 caractères, sans caractère de contrôle.")
        if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ValueError("Saisis une adresse email valide.")
        self.id = id
        self.identifiant = identifiant
        self.email = email
        self.mot_de_passe_hash = mot_de_passe_hash

    @classmethod
    def creer(cls, identifiant: str, email: str, mot_de_passe: str):
        if not 12 <= len(mot_de_passe) <= 128:
            raise ValueError("Le mot de passe doit contenir entre 12 et 128 caractères.")
        # Werkzeug utilise un algorithme de hachage adapté avec un sel aléatoire.
        return cls(identifiant, email, generate_password_hash(mot_de_passe))

    def verifier_mot_de_passe(self, mot_de_passe: str) -> bool:
        return check_password_hash(self.mot_de_passe_hash, mot_de_passe)


class Cave:
    """Une cave est rattachée à un seul utilisateur."""

    MAX_ETAGERES = 5

    def __init__(self, id: int, utilisateur_id: int):
        self.id = id
        self.utilisateur_id = utilisateur_id

    def verifier_proprietaire(self, utilisateur_id: int):
        if self.utilisateur_id != utilisateur_id:
            raise PermissionError("Cette cave n'est pas accessible.")

    def verifier_ajout_etagere(self, nombre_actuel: int):
        if nombre_actuel >= self.MAX_ETAGERES:
            raise ValueError("Votre cave peut accueillir au maximum 5 étagères.")


class Vin:
    """Une référence du catalogue, partagée par plusieurs bouteilles."""

    def __init__(self, domaine: str, nom_cuvee: str, type: str,
                 millesime: int, region: str):
        for valeur in (domaine, nom_cuvee, type, region):
            if not isinstance(valeur, str) or not valeur.strip():
                raise ValueError("Les informations du vin doivent être renseignées.")
        if type not in ("rouge", "blanc", "rosé", "effervescent"):
            raise ValueError("Type de vin inconnu.")
        if not isinstance(millesime, int) or isinstance(millesime, bool) or millesime <= 0:
            raise ValueError("Le millésime doit être un entier positif.")

        self.domaine = domaine.strip()
        self.nom_cuvee = nom_cuvee.strip()
        self.type = type
        self.millesime = millesime
        self.region = region.strip()


class Bouteille:
    """Un exemplaire physique : son vin, son prix et sa date d'ajout."""

    def __init__(self, vin: Vin, prix_achat: float):
        if not isinstance(vin, Vin):
            raise ValueError("La bouteille doit être associée à un vin.")
        if (isinstance(prix_achat, bool)
                or not isinstance(prix_achat, (int, float))
                or not isfinite(prix_achat) or prix_achat < 0):
            raise ValueError("Le prix doit être un nombre positif ou nul.")

        self.vin = vin
        self.prix_achat = prix_achat
        self.date_ajout = date.today()
        self.statut = "en stock"
        self.etagere = None


class Etagere:
    """Un emplacement qui contrôle les ajouts et sa capacité."""

    def __init__(self, nom: str, capacite_max: int):
        if not isinstance(nom, str) or not nom.strip():
            raise ValueError("Le nom de l'étagère est obligatoire.")
        self._verifier_entier_positif(capacite_max)
        self.nom = nom.strip()
        self._capacite_max = capacite_max
        # Chaque étagère possède sa propre liste de bouteilles.
        self._bouteilles = []

    @staticmethod
    def _verifier_entier_positif(nombre: int):
        if not isinstance(nombre, int) or isinstance(nombre, bool) or nombre <= 0:
            raise ValueError("La capacité et les quantités doivent être des entiers positifs.")

    @property
    def capacite_max(self) -> int:
        return self._capacite_max

    @property
    def bouteilles(self) -> list:
        # Une copie permet de consulter le stock sans modifier la liste interne.
        return self._bouteilles.copy()

    def occupation(self) -> int:
        return len(self._bouteilles)

    def peut_accueillir(self, nombre: int) -> bool:
        self._verifier_entier_positif(nombre)
        return self.occupation() + nombre <= self.capacite_max

    def ajouter_bouteille(self, bouteille: Bouteille):
        if not isinstance(bouteille, Bouteille):
            raise ValueError("Il faut ajouter un objet Bouteille.")
        if bouteille.etagere is not None:
            raise ValueError("Cette bouteille est déjà rangée sur une étagère.")
        if bouteille.statut != "en stock":
            raise ValueError("Seule une bouteille en stock peut être rangée.")
        if not self.peut_accueillir(1):
            raise ValueError("L'étagère est pleine.")

        # On modifie le stock seulement après toutes les vérifications.
        self._bouteilles.append(bouteille)
        bouteille.etagere = self

    def changer_capacite(self, nouvelle: int):
        self._verifier_entier_positif(nouvelle)
        if nouvelle < self.occupation():
            raise ValueError("La capacité ne peut pas être inférieure à l'occupation.")
        self._capacite_max = nouvelle
