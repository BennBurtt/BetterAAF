# BetterAAF

Nettoyage d'AAF avant le mixage : clips dupliqués, clips quasi identiques (null test) et clips stéréo qui sont en fait du double mono.

## Utilisation

1. Glisse un `.aaf` dans la fenêtre (ou **Ouvrir un AAF…**). L'analyse démarre toute seule.
2. Si des fichiers son ont été déplacés, indique leur dossier dans **Fichiers son** (ou glisse le dossier dans la fenêtre) puis **Analyser**.
3. Coche ou décoche les clips dans chaque onglet, choisis les actions, puis **Exporter l'AAF corrigé…**.
   L'AAF d'origine n'est jamais modifié.

## Ce qui est détecté

- **Clips dupliqués** : un clip entièrement recouvert, sur une autre piste, par un clip au même audio
  (même média au même calage, ou audio identique à l'échantillon près). On garde la piste la plus haute.
- **Quasi identiques** : même chose, mais le null test donne un résidu sous le seuil (−60 dB par défaut,
  résidu rapporté au plus fort des deux clips).
- **Stéréo double mono** : L − R sous le seuil. Gère les pistes stéréo exportées en multicanal
  (Channel Combiner) et les paires de pistes mono `Piste.L` / `Piste.R`.

## Actions

- Supprimer ou muter les doublons. Trois façons de muter : clip désactivé (mute natif Media Composer),
  gain de clip à −∞, ou déplacement sur une piste « MUTE ».
- Double mono : passage en mono en supprimant ou en mutant le canal R.
  Si une piste stéréo contient au moins un vrai clip stéréo, les clips double mono partent sur une
  nouvelle piste « … MONO » ; si toute la piste est double mono, elle devient mono.
- **AAF autonome** : tous les fichiers son utilisés sont embarqués dans un seul fichier AAF.

## Limites connues

- Les fondus et transitions ne sont pas pris en compte dans le null test ; le gain de clip non plus.
- Les médias MXF (Media Composer) nécessitent ffmpeg installé sur le Mac (`brew install ffmpeg`).
  Les WAV / BWF / AIFF de Pro Tools sont lus directement.
- Un clip stéréo pointant vers un seul fichier entrelacé est analysé mais ne peut pas être converti en mono.
