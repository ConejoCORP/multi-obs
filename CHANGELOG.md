# Changelog

Chaque version publiée avec `python release.py X.Y.Z` reprend la section correspondante :
c'est ce texte que Multi OBS affiche quand il propose la mise à jour.

## 1.1.9

- **Chat du live : Twitch, YouTube ou les deux réunis, avec le fil d'actualité.** Sur la carte de l'OBS du
  live, le logo Twitch devient un vrai bouton **Chat** : un clic ouvre le chat, sa petite flèche (ou un clic
  droit) permet de choisir :
  - **Chat Twitch**, **Chat YouTube** (Multi OBS trouve tout seul ton live en cours sur ta chaîne) ou le
    **multi-chat**, les deux réunis avec un logo Twitch ou YouTube devant chaque message pour savoir d'où
    vient la personne.
  - **Chaque OBS du live a son chat**, dans sa propre fenêtre : par exemple le chat Twitch pour l'OBS du
    live Twitch et le chat YouTube pour celui du live YouTube, ouverts côte à côte, chacun à sa place.
  - Le chat s'ouvre dans une fenêtre de Multi OBS : le chat en haut, le **fil d'actualité** en bas (20 % de
    la hauteur, à régler à la souris) avec les abonnements et abonnements offerts, raids, bits, Super Chats
    et Super Stickers, nouveaux membres YouTube, annonces et récompenses des points de chaîne, à l'heure près.
    Dans la police du chat de Twitch (Inter), pseudos en couleur et en gras, emotes affichées, Super Chats,
    abonnements et raids mis en avant dans le chat, messages supprimés par les modos grisés, taille du
    texte, « Toujours devant ». La fenêtre se lit seulement : ses boutons ouvrent le vrai chat quand tu
    veux écrire. Lancée avant ton live YouTube, elle l'attend et s'y branche.
  - Les follows Twitch et les nouveaux abonnés YouTube ne passent pas par le chat : le bouton
    **Flux Twitch complet** ouvre le flux d'activité de Twitch (follows compris), avec ton compte connecté
    dans Chrome ou Edge.
  - Tout en bas du menu, **Personnalisé…** règle tout : ce que le bouton de cette instance ouvre, tes chaînes, le fil
    d'actualité (affiché ou non, sa hauteur, ce qu'il montre), la taille du texte, « Toujours devant », et
    au choix le vrai chat du site pour Twitch ou YouTube seul (pour écrire, sans fil d'actualité).

## 1.1.8

- **Stream Deck : 97 pictos au lieu de 44** pour l'apparence des touches « Action OBS », rangés par
  groupes dans les réglages de la touche :
  - **Flèches** : gauche, droite, haut, bas, les 4 diagonales, gauche-droite, haut-bas, précédent / suivant,
    retour, recharger, échanger, plein écran.
  - **Scènes et écrans** : ordinateur portable, fenêtre, écran partagé, incrustation, mosaïque, zoom,
    curseur, clavier, souris… en plus de ceux d'avant.
  - **Moments du live** : sablier (« ça commence » / BRB), repas, jour, planning, annonce, cadeau,
    couronne, fusée…
  - **Personnes, Jeu, Son, Symboles, Commandes** : groupe, sourire, viseur, épée, bouclier, tête de mort,
    dé, croix, plus, moins, info, attention, question, cadenas, repère, rond, carré, triangle, losange…
- Le plugin Stream Deck passe en 1.1.8 : Multi OBS propose de le mettre à jour (en bas à droite).

## 1.1.7

- **Stream Deck : touche « Fermer Multi OBS »**. Maintiens-la (un cercle blanc grandit) et Multi OBS se
  ferme, en suivant ton réglage ⚙ → Général → « À la fermeture de Multi OBS » : tes OBS s'éteignent avec
  « Tout éteindre », continuent avec « Laisser tourner les instances », et avec « Demander » Multi OBS passe
  devant pour te poser la question. Un simple appui affiche « Maintiens pour fermer » ; l'appui simple peut
  suffire si tu préfères (réglages de la touche).
  - Refusée pendant un enregistrement ou un live, pour ne rien perdre ; une conversion MP4 en cours est
    terminée d'abord. Pendant un rec, la touche est grisée.
  - Quand Multi OBS est fermé, un appui sur la touche le rouvre.
- Le plugin Stream Deck passe en 1.1.7 : Multi OBS propose de le mettre à jour (en bas à droite).
- Mise à jour : l'installation ne s'arrête plus sur une erreur quand deux mises à jour se suivent sans que
  Multi OBS ait été relancé entre les deux.

## 1.1.6

- **Stream Deck : ton image, ton texte ou un picto sur les touches « Action OBS »** (idéal pour les
  changements de scène). Dans les réglages de la touche, sous l'action, **Apparence → Personnaliser** :
  - **Image** : choisis-la (ou glisse-la). Une photo remplit la touche, un logo transparent est posé entier
    sur la couleur de la touche. Elle est enregistrée avec la touche et suit les profils exportés.
  - **Picto** : 44 pictos au choix (manette, webcam, micro, discussion, pause café, étoile…).
  - **Texte** : remplace le nom de la scène ou de l'action (les emojis marchent), ou « Aucun » pour ne
    garder que l'image ou le picto, en plus grand. Sans picto, le texte seul s'affiche en grand.
  - Les couleurs d'état restent : une scène à l'antenne met un cadre vert autour de l'image, une source
    cachée voile l'image, et pendant un rec ou un live le timecode reste affiché.
  - Avec un appui long, chaque moitié de la touche a sa propre apparence.
- Le plugin Stream Deck passe en 1.1.6 : Multi OBS propose de le mettre à jour (en bas à droite).

## 1.1.5

- **Compatible OBS 33** : OBS 33 range ses modules dans un nouveau dossier `core`, et la préparation des
  instances s'arrêtait sur l'erreur « No such file or directory … obs-virtualcam-module64.dll ». Multi OBS
  reconnaît maintenant les deux organisations : caméras virtuelles, synchro à l'image près et préparation
  marchent avec OBS 33 comme avec les versions d'avant. Les instances déjà préparées sous OBS 33 sont
  reconstruites automatiquement (scènes et réglages conservés).
- Si les caméras virtuelles ne peuvent pas être installées, les OBS s'allument quand même (un message le
  signale en bas de la fenêtre) au lieu de s'arrêter sur une erreur.

## 1.1.4

- **Chat Twitch en un clic** : le logo Twitch sur la carte de l'OBS du live (type Live, ou « live » dans son
  nom) ouvre le chat de ta chaîne, en thème sombre, dans une fenêtre à part (fenêtre de Chrome ou d'Edge sans
  onglets, avec ton compte Twitch déjà connecté). Le lien de la chaîne se met dans ⚙ → Général → Twitch, ou
  il est demandé au premier clic.

## 1.1.3

- **Nom de la vidéo demandé au lancement du rec** (option, désactivée par défaut : ⚙ → Enregistrements) :
  pour ne plus oublier de le changer d'une vidéo à l'autre. Au lancement d'un rec (Tout enregistrer, Rec
  d'une carte, Stream Deck, raccourci), une petite fenêtre propose le nom actuel, déjà sélectionné : tape
  le nouveau, Entrée, et le rec part avec ce nom. Pas redemandé pour un OBS lancé pendant qu'un autre
  enregistre déjà. Sur le Stream Deck, la touche affiche « Nom de la vidéo ? » et la fenêtre passe devant.
- **Synchros automatiques désactivables** : ⚙ → Enregistrements → « Synchro à l'image près », trois cases
  (toutes cochées par défaut) :
  - **Aligner le départ des recs** : décochée, plus de courte pause dans la première seconde des fichiers,
    mais les recs démarrent chacun quand ils peuvent (quelques images d'écart) ;
  - **Finir tous les recs sur la même image** ;
  - **Recaler les fichiers à chaque reprise après une pause**.
  La pause elle-même reste toujours faite à la même image partout.

## 1.1.2

- **Le live en violet** : la pastille « LIVE » avec la durée du live, en haut à gauche de l'aperçu, et
  l'étiquette « LIVE » à côté du nom de l'instance sont maintenant violettes, pour ne plus les confondre
  avec le rec (qui reste rouge). Couleur au choix dans ⚙ → Aperçu → « Couleur du live ».
- **Fichiers sans ton nom** : décoche la case « Nom » en haut pour les enregistrements solo
  (`cam-Tuto-2026-10-24_…` au lieu de `Stund-cam-Tuto-…`). Ton nom reste écrit, prêt à revenir, et le
  réglage est gardé dans les préconfigs.
- **Aperçus fluides derrière un jeu** : quand un jeu est au premier plan, Windows 11 ralentissait Multi OBS
  (minuteries imprécises, processeur en mode économie) et les aperçus saccadaient : jusqu'à 18 % d'images
  en retard mesurées avec le processeur chargé. Multi OBS se protège maintenant lui-même, comme il le
  faisait déjà pour ses OBS (⚙ → SafeGuard → « Empêcher Windows de ralentir les OBS et Multi OBS »).
- **Prévenu si une conversion en MP4 n'est pas finie** : éteindre une instance, « Tout éteindre » ou fermer
  Multi OBS pendant qu'OBS convertit le rec en MP4 demande maintenant quoi faire : éteindre à la fin de la
  conversion (proposé par défaut : la barre affiche « puis extinction », et Multi OBS reste ouvert jusque-là
  s'il le faut), éteindre maintenant (le MP4 ne sera pas fini, le MKV reste complet) ou annuler. Sur le
  Stream Deck, la touche affiche « Arrêt après la conversion ». Et une instance éteinte pendant un rec attend
  maintenant la vraie fin de la conversion (avant : 30 s au plus, trop court sur un disque dur).
- **Plus de clic perdu sur « Tout en pause » / « Tout reprendre »** : cliqué juste après « Tout
  enregistrer » (pendant que les recs démarrent et s'alignent), ou « Tout reprendre » cliqué dès que la
  pause s'affiche, le clic pouvait être ignoré sans rien dire, surtout quand le PC est chargé. La pause est
  maintenant faite dès que le départ est aligné (deux clics pendant ce temps s'annulent), et la reprise est
  acceptée dès que la pause est faite partout. Le double-clic accidentel reste sans effet.
- **Plus de saccades pendant la conversion en MP4** : pendant qu'OBS convertit sur un disque dur, lire la
  taille du MP4 (pour la barre de conversion) bloquait jusqu'à 0,7 s, et toute la fenêtre de Multi OBS se
  figeait, aperçus compris. Les accès disque se font maintenant à part : l'interface ne bloque plus (23 ms
  au pire, mesuré avec un disque qui met 0,7 s à répondre). Utile aussi au réveil d'un disque en veille.
- **Aperçu plus régulier** : les images étaient demandées toutes les 16 ms au lieu de 16,67 ms à 60 i/s ;
  une demande sur 25 retombait sur l'image déjà reçue, d'où un petit à-coup régulier. Images en retard :
  de 5 % à 1,5 %.

## 1.1.1

- **Nouvel onglet SafeGuard** dans les paramètres (⚙) : tout ce qui surveille tes recs et tes lives au
  même endroit.
  - **Silence** : « Pas de son » n'apparaît plus qu'après **2 minutes** sans aucun son (avant : 10 s),
    réglable de 10 s à 10 min, ou jamais. Le silence n'est compté que pendant le rec : ni avant, ni
    pendant une pause.
  - **Images perdues** : avertissement à partir du nombre d'images que tu choisis (10 par défaut).
    Une rafale (6 images en 10 s) donne toujours l'alerte rouge.
  - **Disque presque plein** : seuil de l'alerte, de 2 à 100 Go (10 par défaut).
  - **Où prévenir** : faire clignoter Multi OBS dans la barre des tâches, alerte rouge sur le Stream
    Deck (les deux activés par défaut). Le bandeau rouge reste toujours affiché.
  - **Stabilité des OBS** : priorité des OBS sur les jeux, et OBS jamais ralentis par Windows en
    arrière-plan (les deux conseillés, activés par défaut).

## 1.1.0

- **Départ et arrêt des recs à l'image près** : avec « Tout enregistrer », les fichiers commencent
  maintenant sur la même image (avant : jusqu'à 7 images d'écart, selon l'OBS qui démarrait le plus vite).
  « Tout arrêter » les finit tous sur la même image : **même nombre d'images dans chaque fichier**, les
  pistes se calent d'un coup au montage. Un petit module OBS livré avec Multi OBS donne l'heure exacte de
  la première image de chaque fichier ; juste après le départ, les instances parties en avance font une
  très courte pause pour s'aligner, puis c'est revérifié. Mesuré image par image sur 65 départs et
  65 arrêts, avec plusieurs réglages (NVENC HEVC 4K/1440p, NVENC simple, x264, 30 et 60 i/s, son retardé
  de 0,6 s sur une instance) : tous alignés au mieux (il reste au plus une fraction d'image, car les
  horloges d'images des OBS ne sont pas en phase).
  - Après la mise à jour, **éteins puis rallume tes instances** une fois pour charger le module.
- **Chaque pause resynchronise** : à la reprise, Multi OBS mesure l'écart exact entre les fichiers et le
  rattrape dans la pause, sans coupure de plus.
- **Alertes immédiates** quand un rec a un souci : rec bloqué (plus rien n'est écrit), OBS fermé ou planté
  pendant le rec, arrêt qui ne se termine pas, erreur affichée par OBS, son qui décroche ou micro perdu,
  encodeur en panne, écriture du fichier impossible, carte graphique perdue, images perdues, disque
  presque plein, capture de jeu impossible (un seul OBS peut capturer un même jeu : les autres
  enregistraient du noir sans prévenir ; prends une capture de fenêtre dans les autres instances). Bandeau rouge en haut de Multi OBS, carte en rouge, bouton de la barre des tâches qui
  clignote, et cadre rouge sur les touches du Stream Deck. Aucun son : rien ne s'entend dans tes recs.
- **Images plus régulières dans les recs** : Windows 11 bride les programmes réduits ou en arrière-plan ;
  OBS perdait alors sa minuterie précise et rendait chaque image jusqu'à 15 ms après son heure (petites
  saccades dans les mouvements). Multi OBS retire ce bridage pour ses OBS : moins de 2 ms.
- **Les OBS passent devant les jeux** : quand un jeu sature le processeur, l'encodeur d'OBS décrochait et
  répétait des images (saccades). Les OBS sont maintenant en priorité « au-dessus de la normale ». Dans
  notre test le plus dur (carte graphique et processeur à 100 % en même temps, 3 OBS dont un en 4K) :
  12 à 14 % d'images répétées avec des OBS lancés seuls, 0 à 0,1 % avec Multi OBS (une fois 7 % pour
  l'instance 4K, la plus lourde : l'alerte « images perdues » prévient alors). Réglable dans ⚙ → Général
  → Performances ; un autre choix fait dans OBS est respecté.
- **Rec relancé juste après un arrêt** : il pouvait ne pas démarrer, sans rien dire (OBS ferme encore
  l'encodeur du rec précédent et ignore la demande). Multi OBS attend maintenant qu'OBS ait fini, vérifie
  que chaque rec démarre vraiment (sinon il redemande, puis alerte). 0 raté sur 100 cycles arrêt / départ
  enchaînés, contre 3 à 4 avant.
- L'alerte « Images perdues » n'apparaît plus pour quelques images (souvent au démarrage d'OBS, sans
  effet visible) : seulement à partir de 10 images perdues au total. Pour être prévenu dès la première :
  ⚙ → Général → « Prévenir dès la première image perdue ». Le total reste affiché en petit sur la carte.

## 1.0.8

- **Plugin Stream Deck** : pilote chaque OBS, ou toutes les instances d'un coup, avec des touches
  colorées et lisibles. Rouge = enregistre, orange = en pause, vert = allumé / actif, gris = éteint. En
  haut de chaque touche, la couleur et le nom de l'OBS, ou « TOUS » avec les couleurs des instances
  concernées (atténuées si elles sont éteintes).
  - **Enregistrer** avec le timecode sur la touche ; au choix, appui court = pause et appui long =
    rec / stop, pour ne jamais couper un rec par erreur.
  - **Pause** (à l'image près avec « Tous »), **Allumer / Éteindre**, **Caméra virtuelle**, **Voir OBS**.
  - **Action OBS** : changer de scène, montrer / cacher une source, activer / couper un filtre, couper
    le son, lancer le live, capture d'écran, rec, pause… et une 2e action en appui long si tu veux. Les
    touches suivent OBS en direct (scène à l'antenne en vert, source cachée, son coupé en rouge).
  - Appui long : un cercle blanc grandit jusqu'à couvrir la touche, puis l'action part.
  - Sécurité : éteindre un OBS qui enregistre est refusé. Si Multi OBS est fermé, un appui sur une
    touche l'ouvre.
- **En bas à droite de Multi OBS** : « Stream Deck connecté / déconnecté », ou un bouton pour installer
  (ou mettre à jour) le plugin en un clic, sans passer par le Marketplace. Aussi dans ⚙ → Stream Deck.
  Le plugin se connecte tout seul à Multi OBS, en local (cet ordinateur seulement).
- **Pause à l'image près encore plus sûre** : OBS ignore une pause demandée juste après une reprise
  (tout en répondant « OK »), ce qui pouvait laisser une instance en rec quand les autres s'arrêtaient.
  Multi OBS laisse maintenant à OBS le temps qu'il faut, et redemande si besoin, toujours avec le même
  nombre d'images enlevées partout (0 écart sur 20 pauses, dont 10 juste après une reprise).

## 1.0.7

- **Pause globale à l'image près** : « Tout en pause » / « Tout reprendre » (et le raccourci) enlèvent
  exactement les mêmes images dans chaque enregistrement. Avant, chaque OBS coupait sur sa propre horloge
  et les pistes pouvaient se décaler d'une image à chaque pause (jusqu'à 6 images après 20 pauses dans nos
  mesures). Maintenant : 0 image d'écart sur 100 pauses mesurées à 60 i/s (dont 40 aperçus allumés). Si OBS
  tarde à appliquer une pause, Multi OBS compense, ou te prévient en bas de la fenêtre.
- **Caméras virtuelles au nom de leur couleur** : « VCam Rouge », « VCam Bleu »… dans Discord, Zoom ou OBS
  (avec le numéro si deux instances ont la même couleur). Les caméras existantes sont renommées toutes
  seules ; redémarre Discord pour voir les nouveaux noms.
- **Conversion MP4 sur la carte** : pendant qu'OBS convertit l'enregistrement, une barre au style d'OBS
  remplace la barre de stockage, avec l'avancement (aussi sur la pastille d'une instance réduite). La petite
  fenêtre de conversion d'OBS reste derrière, et ne déclenche plus l'alerte « OBS attend une réponse ».
- Un OBS figé ne peut plus figer Multi OBS quand il réduit ou affiche sa fenêtre.

## 1.0.6

- Aperçu : quand la scène n'a pas le même format que la carte, les bandes autour de l'image prennent
  **la couleur de l'instance** au lieu du noir, pour ne plus les confondre avec du vide dans la scène OBS.
- Les badges (REC, VCam active) se placent dans les coins de l'image.

## 1.0.5

- Le bouton qui range une instance dans la barre « Instances réduites » a une icône plus parlante :
  un **œil barré** (« masquer »), à la place du petit trait.

## 1.0.4

- **Réduire une instance** : le bouton **—** en haut de sa carte la range dans la barre
  **« Instances réduites »** en bas (l'OBS du live quand tu ne lives pas…). Les autres cartes prennent la place.
- **« Remettre »** (ou double-clic sur la pastille) la ramène en haut, à sa place parmi les autres.
- Une instance réduite garde son état sous les yeux : Éteinte, Actif, REC avec la durée, VCam allumée, alertes.
  Son aperçu est arrêté (aucune ressource) et **« Tout allumer » la laisse de côté**.
- Caméras virtuelles : la réparation automatique n'abandonne plus après un échec (elle réessaie de plus en
  plus espacé), et « Caméras virtuelles : x/y installées » en bas est remis à jour chaque minute.

## 1.0.3

- **Crée tes propres types d'enregistrement** : Paramètres → Enregistrements → Ajouter, Renommer,
  Supprimer (Cam, Écran et Live par défaut). B-roll n'est plus proposé ; une instance qui l'utilisait le garde.
- **Link** (icône chaîne) remplace la case « Synchro » : une instance liée suit les boutons du haut
  et les raccourcis, une instance déliée reste à part (pratique pour le live).
- **Icône dossier** à droite des boutons de chaque instance : ouvre son dossier d'enregistrement.
- Une instance allumée affiche **« Actif »** (au lieu de « Prête »).
- **Caméras virtuelles plus fiables** : si elles disparaissent de Windows, Multi OBS les remet tout seul
  (au démarrage, puis chaque minute) au lieu d'attendre le prochain allumage des instances.
  « Désinstaller » dans les paramètres est maintenant définitif, jusqu'à « Installer / réparer ».
- **Paramètres en onglets** (Général, Enregistrements, Aperçu, Caméras virtuelles, Raccourcis) :
  la fenêtre tient sur un écran 1080p.
- Le changelog de la fenêtre de mise à jour est lisible (il s'affichait en blanc sur blanc).
- Boutons des instances moins serrés : « Reprendre » n'est plus coupé.

## 1.0.2

- Nouveau type d'enregistrement **Live** (en plus de Cam, Écran et B-roll) pour le nom des fichiers.
- Les raccourcis clavier globaux sont **désactivés par défaut** (à activer dans les paramètres).
- En venant de la 1.0.0 / 1.0.1, les réglages restés sur les anciens défauts passent aux nouveaux
  (aperçu 60 i/s en 1/4, identique en arrière-plan, raccourcis désactivés). Tes choix perso sont gardés.
- Fermeture de Multi OBS avec des instances allumées : case **« Se souvenir de mon choix »**, et réglage
  « À la fermeture de Multi OBS » dans les paramètres (Demander / Laisser tourner / Tout éteindre).
- Installateur : options **Ajouter au menu Démarrer** et **Raccourci sur le bureau** (cochées par défaut).
  Corrige les raccourcis qui n'étaient pas créés en 1.0.0 / 1.0.1.
- Quand une caméra virtuelle est éteinte, son image d'attente prend **la couleur de son instance**
  (dans Discord, OBS…). Redémarre l'appli qui l'affiche pour voir la nouvelle image.

## 1.0.1

- **Un seul fichier à télécharger** : l'installateur contient Multi OBS. Les mises à jour passent par lui.
- **Aperçus par défaut** : 60 images/s, qualité 1/4, même rafraîchissement quand Multi OBS est en arrière-plan.
- **Nouvelles instances sur NVIDIA NVENC** aussi en mode de sortie avancé (QP constant pour l'enregistrement) :
  plus d'encodeur AMD de la puce graphique intégrée proposé par défaut.
- Barre de nommage : « output » montre le nom du prochain fichier.

> Depuis la 1.0.0 : « Mettre à jour » ouvre la page de la version. Ferme Multi OBS, télécharge
> `Multi-OBS-Setup.exe` et lance-le.

## 1.0.0

Première version.

- **Instances OBS indépendantes** : chaque instance est un vrai OBS portable avec ses propres scènes,
  profils, réglages et plugins. Plus de plantage quand on modifie l'une d'elles.
- **Une caméra virtuelle par instance** (« OBS Virtual Camera 1, 2, 3… »), sans droits administrateur.
- **Pilotage global** : tout allumer / éteindre, tout enregistrer, tout mettre en pause, raccourcis
  clavier globaux, case « Synchro » pour garder une instance à part.
- **Aperçu en direct sans compression** de chaque instance, avec vumètres audio, images perdues,
  CPU et alertes (pas de son, disque presque plein).
- **Aperçu économe** : jamais plus de pixels que l'écran n'en affiche, pause automatique (fenêtre
  réduite, carte hors écran, session verrouillée, écran éteint), ralenti tout seul si OBS perd
  des images. Par défaut 60 i/s en 1/4, option pour ralentir en arrière-plan.
- **Nommage automatique des fichiers** : `Nom-type-Vidéo-date_heure`, type Cam / Écran / B-roll par instance.
- **MKV converti en MP4** automatiquement à la fin de l'enregistrement (option : MKV à la corbeille).
- **Préconfigs** : enregistre et rouvre un ensemble complet d'instances.
- **Barre des tâches** : chaque instance s'appelle « OBS Blue — Caméra » avec le logo OBS à sa couleur.
- **Espace disque** et dossier d'enregistrement visibles sur chaque carte.
- **Installateur unique** (`Multi-OBS-Setup.exe`, sans droits admin) et **mises à jour** depuis GitHub,
  avec le changelog. Les nouvelles instances utilisent NVIDIA NVENC quand la carte est présente.
