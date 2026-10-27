Fonts used by the character builder

Nunito  (nunito-latin.woff2, nunito-latin-ext.woff2, Nunito-OFL.txt)
  The wiki's page font. SIL Open Font License; the licence is in Nunito-OFL.txt.

Pretendard  (pretendard-latin-400.woff2, pretendard-latin-600.woff2, Pretendard-OFL.txt)
  The body text of the identity teaser pictures. Used only by the Visualizer tab. These two files are Pretendard Regular and
  SemiBold cut down to the Latin letters, digits and punctuation (the full font is 700 KB a file). SIL Open Font License.

Bebas Neue  (bebas-neue-latin.woff2, BebasNeue-OFL.txt)
  The tall condensed lettering of the identity teaser pictures (IDENTITY INFO, SKILL 1, PASSIVE, the WARNING strip). Used only by
  the Visualizer tab. SIL Open Font License.

Mikodacs  (mikodacs.ttf, NOT in the repository yet)
  The font the Limbus Company wiki uses for status names, skill names and descriptions, and the one the identity teaser pictures
  use for names and titles. Designer: gluk (glukfonts.pl). It is listed as free for personal and commercial use under the
  SIL Open Font License, e.g. at https://www.fontspace.com/mikodacs-font-f13566

  To switch it on:
    1. Download the font and unzip it.
    2. Copy the regular file ("Mikodacs", not "MikodacsPCS") here and name it exactly: mikodacs.ttf
       (lower case: the page is served from GitHub Pages, which is case sensitive).
    3. Copy the licence text that came with the font next to it, e.g. as Mikodacs-OFL.txt.
    4. Commit and push. Nothing else changes: style.css already asks for it.

  Until the file is here the page shows Nunito everywhere, and the Visualizer draws names and titles in heavy Pretendard (the browser
  logs one 404 for mikodacs.ttf, which is harmless).
