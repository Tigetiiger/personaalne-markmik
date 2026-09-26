# Märkmiku kujundus — „Riba“

Valitud 2026-09-26 kolme iteratsiooni järel (`kujundus-variandid.html` → `-2.html` → `-3.html`,
variant 1). Klikitav näidis: `docs/kujundus-riba.html`. See fail on lühike spetsifikatsioon, mille
järgi `site/style.css` ja mallid ümber teha.

## Idee

Tume tööriist, mitte leht. Neutraalne must, üks tsüaan aktsent, süsteemifont. Navigatsioon on
ülemises servas ühes ribas; riba all vasakul märkmete nimekiri, paremal sisu. Kalender, Graaf,
Postkast ja Seaded on ilma nimekirjata, täies laiuses.

## Värvid

```css
:root[data-teema="tume"] {
  --bg:    #0f1115;  /* leht */
  --p1:    #15181e;  /* riba, külgpaneel, kaardid */
  --p2:    #1c2028;  /* hover, aktiivne rida, sündmus */
  --ln:    #262b34;  /* jooned */
  --tx:    #e6e8ec;  /* tekst */
  --mu:    #8b93a1;  /* teisene tekst, ikoonid, sildid */
  --ac:    #4fd1e8;  /* aktsent: aktiivne leht, lingid, primaarne nupp, täna */
  --acfg:  #031317;  /* tekst aktsendi peal */
  --ok:    #5fd38d;
  --warn:  #f2c94c;  /* kontrolltööd */
  --bad:   #f27a6f;  /* hilinenud, tähtajad, vead */
  --today: rgba(79, 209, 232, .05);
  --r: 6px;
}
```

Hele teema jääb praeguse `style.css` paberi-tokenite peale; see spets katab tumeda. Kalendri
sündmuse liigid: `tund` hall (`--mu` tekst, `--ln` serv), `kt` `--warn`, `isik` `--ac`,
`taht` `--bad`, pakutud = katkendlik `--mu` raam ilma taustata.

## Tüpograafia

- Kõik: `system-ui, -apple-system, "Segoe UI", sans-serif`, 13,5 px / 1,5. Kellaajad
  `font-variant-numeric: tabular-nums`.
- Lehe pealkiri 1,55 rem / 650, `letter-spacing: -.01em`. Märkme h1 1,9 rem, h2 1,1 rem.
- Jaotise pealkiri (`Päevakava`, `Ülesanded`): 0,75 rem, suurtähed, `letter-spacing: .07em`,
  `--mu`, 600.
- Märkme sisu 0,96 rem / 1,65, mõõt kuni 62 rem.

## Ülariba (3 rem, sticky, `--p1`, alumine joon `--ln`)

Vasakult: bränd (tsüaan ruut „M“ + „Märkmik“) · lehed tekstina (Täna, Märkmed, Kalender, Graaf,
Postkast + tsüaan number-silt) · paremal otsing (`⌘K`), tsüaan „+ Uus“, seadete hammasratas.
Aktiivne leht: `--ac` tekst `--p2` taustal, nurgad 6 px. Hover: `--tx` `--p2` peal.

## Riba all

- Täna ja Märkmed: `grid-template-columns: 15rem minmax(0, 1fr)`. Vasak paneel sticky, `--p1`,
  parem joon `--ln`; sees „+ Uus märge“ (raamiga nupp) ja puu: Kinnitatud, Hiljutised, Kaustad,
  Sildid. Aktiivne märge `--p2` tausta ja `--ac` tekstiga.
- Kalender, Graaf, Postkast, Seaded: üks veerg.
- Sisu padding `1.2rem 1.5rem 4rem`, laius kuni 64 rem (kalender ja graaf täies laiuses).
- Iga lehe kohal leivapuru-rida 0,8 rem `--mu`; lehe nupud (Eile/Homme, Jaga/Ajalugu/AI,
  Nädal/Kuu/Nimekiri/+ Sündmus) samal real paremal.

## Elemendid

- Nupud: raamita, `--mu` tekst, hover `--p2`. Primaarne: `--ac` raam ja tekst. Tugev
  (`+ Uus`, `+ Sündmus`): `--ac` taust, `--acfg` tekst. Aktiivne lüliti: `--p2` taust.
- Read (päevakava, ülesanded, postkast, seaded): `padding .4rem 0`, ülemine joon `--ln`, mitte
  kaardid. Kolm statistikakaarti Täna-lehe ülaosas on ainus kaardirida.
- Sildid `.chip`: `--ln` raam, 6 px. AI-märk: `--ac` taust, 0,62 rem, 700.
- Fookus: `outline: 2px solid var(--ac)`.
- Telefon (< 900 px): vasak paneel peitub, riba lehed kerivad külgsuunas, otsing peitub.

## Klaviatuur

`⌘K` palett, `g t / g m / g k / g g / g p` lehed, `n` uus märge (näidises töötavad `g`-klahvid).
