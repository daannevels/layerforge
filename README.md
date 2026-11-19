# Layerforge

Layerforge maakt **nieuwe drum one-shots** uit 2 tot 4 van je eigen samples van hetzelfde soort
(kick, clap, crash, open hat, closed hat of perc). Je kiest per sample welk stuk je mooi vindt,
bijvoorbeeld de body van kick 1 en de top van kick 2. Layerforge mengt die stukken tot nieuwe samples.
Alles draait op je eigen computer. Er gaat niets naar internet.

> **Status: mijlpaal M3c.** Bass en synth mengen nu op een eigen manier ("morph"): één klank met één
> gezamenlijke volumecurve en lengte. Drums werken zoals in M3b.
> Luister-gereedschap (solo, mono, loop) komt in mijlpaal M4.

## Downloaden

1. Ga op GitHub naar deze repository.
2. Klik op de groene knop **Code** en dan **Download ZIP**.
3. Pak het ZIP-bestand uit (dubbelklik erop).

## Starten

Open de uitgepakte map en dubbelklik:

- **Windows:** `Start Layerforge.bat`
- **Mac:** `Start Layerforge.command`

De eerste keer installeert hij wat hij nodig heeft. Dat duurt ongeveer een minuut.
Daarna opent je browser vanzelf de pagina van Layerforge.

Laat het zwarte venster openstaan zolang je Layerforge gebruikt. Sluit het venster om te stoppen.

## Zo gebruik je het

1. Kies bovenaan het **Sound type**: kick, clap, closedhat, openhat, crash, perc, bass of synth.
   Elk type heeft een eigen profiel (zie "Wat doet elk instrument anders?" hieronder).
2. Sleep een sample in een vak, of klik op het vak om een bestand te kiezen. Laad er 2 tot 4.
   Met **+ Add sample slot** maak je een extra vak. Met **✕** haal je een sample weg.
3. Bij **Direction** kies je per deel welk sample de richting geeft:
   - **Low** = het laag (bij een kick: Sub + Body)
   - **Mid** = het midden
   - **High** = de top en de lucht
   Klik op het nummer van een sample, of op **Free**. Free betekent: Layerforge kiest zelf, en dat is per
   variatie anders. Je hoeft niets te kiezen; alles op Free mag ook.
4. Kies hoeveel **Variations** je wilt (1 tot 16) en hoeveel ze mogen afwijken (**Variation amount**:
   0% = alleen precies jouw keuze, 100% = veel verschil).
5. Klik **Generate**. Onder **Results** verschijnt een kaart per variatie. **v01** is altijd de "basis":
   precies jouw keuze, zonder toeval. De andere variaties zijn zo gekozen dat ze duidelijk van elkaar
   verschillen.
6. Luisteren: klik op een kaart. Met de pijltjestoetsen ga je naar de volgende kaart, met de spatiebalk
   speel je af of stop je. **A/B stack** wisselt tussen de variatie en de "simpele stapel" (alle samples
   gewoon op elkaar). Alles speelt even luid af, zodat harder niet wint.
7. Bewaren: **★** zet een kopie in je map `Favourites`. **Download** slaat de WAV op. Je kunt een kaart ook
   rechtstreeks naar je DAW slepen (werkt in Chrome en Edge). **Recipe** toont alle getallen van die blend.

8. **Feedback**: klik op een kaart op **Feedback**. Kies duim omhoog of omlaag, klik de labels aan die
   kloppen (bijv. "Klinkt als 2 lagen", "Staart klopt niet") en typ eventueel wat je hoort. Er wordt
   **geen geluid** bewaard: alleen de getallen van de blend en jouw oordeel, één regel tekst per keer
   (een paar kB) in het bestand `feedback.jsonl` in je Layerforge-map. Met **Export feedback** bovenaan
   download je alles in één bestand. Stuur dat naar Claude: dan worden de profielen ermee bijgesteld.

**Seed en Lock.** De seed is het startgetal voor het toeval. Elke klik op Generate kiest een nieuwe seed,
dus je krijgt steeds nieuwe variaties. Zet **Lock** aan om dezelfde seed te houden: met dezelfde samples,
keuzes en instellingen krijg je dan precies dezelfde variaties terug. **New seed** kiest een nieuw getal.

In het laag mengt Layerforge nooit twee samples door elkaar, want lage tonen kunnen elkaar uitdoven.
Daar wint altijd één sample. Variaties waarbij het laag toch zwakker wordt, gooit Layerforge zelf weg.

## Wat doet elk instrument anders?

| Type | Laag (nooit gemengd) | Uitlijnen | Aanslag | Lengte volgt |
|---|---|---|---|---|
| kick | tot 200 Hz | op de golf (fase), Air op de omhullende | mag mengen | Low |
| clap | – | op de omhullende (de klapjes), ±3 ms | van één clap | Mid |
| closedhat, openhat, crash | – | boven 300 Hz op de omhullende | van één sample | High |
| perc | – | onder 2 kHz op de golf, erboven op de omhullende | van één sample | Mid |
| bass | tot 250 Hz (Sub + Body) | laag op de golf; erboven **morph** | morph | Low |
| synth | tot 200 Hz | laag op de golf; erboven **morph** | morph | Mid |

- **Uitlijnen op de golf** = de golven precies op elkaar leggen, zodat ze elkaar versterken in plaats van
  uitdoven. Belangrijk voor tonen (kick, bass, synth).
- **Uitlijnen op de omhullende** = de luidheidscurves op elkaar leggen. Voor ruis (hats, crash, clap)
  zegt de golf niets, de vorm van de klap wel.
- **Aanslag van één sample** = geen dubbele klap ("flam").
- **Lengte**: in elke band komt de staart van hetzelfde sample als het lijf. Samples die langer zijn dan
  de "lengte-leider" sterven even snel uit als de leider. De leider is het sample dat jij kiest bij de groep
  in de laatste kolom. Staat die op Free, dan kiest Layerforge per variatie een lengte tussen de kortste en
  de langste in.
- **Bass en synth mengen anders dan drums.** Drums worden per band en per tijdzone uit stukjes van je
  samples opgebouwd. Bij bass en synth gebeurt dat alleen in het laag (één sample, voor een strak laag).
  Daarboven "morpht" Layerforge de klankkleur van alle samples tot één nieuwe klank: per frequentie een
  mengsel van de samples, met één fase, zodat het als één geheel klinkt en niet als losse lagen.
  Kies je bij een groep een sample, dan weegt dat sample zwaarder (70%) in die groep.
- **Eén volumecurve voor bass en synth.** Alle lagen volgen hetzelfde volumeverloop en stoppen samen,
  met één gezamenlijke uitfade. Kies je bij de lengte-groep (bass: Low, synth: Mid) een sample, dan krijgt
  de blend precies de lengte van dat sample. Op Free ligt de lengte tussen je kortste en langste sample in.
- **Toonhoogte**: bij bass en synth zie je per sample de noot (bijv. C2). Octaven (C2 en C4) passen bij
  elkaar. Hebben je samples echt andere noten (C en D), dan krijg je een waarschuwing, want die botsen.

## Opruimen

Layerforge bewaart alleen de **20 nieuwste** projecten in `Projects`. Oudere projecten worden automatisch
verwijderd (bij het starten en na elke Generate). Je **Favourites** worden nooit verwijderd, en je
`feedback.jsonl` ook niet. Wil je een variatie houden? Geef hem een ★ of download hem.

**Waar staan mijn bestanden?** Klik op **Open Layerforge folder**. Het is de map `Layerforge` in je
persoonlijke map (Mac: `/Users/<jouw naam>/Layerforge`, Windows: `C:\Users\<jouw naam>\Layerforge`).
Elke keer dat je op Generate klikt, komt er een map bij in `Projects`. Daarin staan een kopie van je
samples (`inputs`), de variaties (`variations/v01.wav`, `v02.wav`, ...), per variatie het recept
(`v01.json`) en `simple_stack.wav`. Je sterren staan in `Favourites`.

## Controleren of alles werkt

Dubbelklik **`Check Layerforge.bat`** (Windows) of **`Check Layerforge.command`** (Mac).
Je ziet een paar regels met `OK` en onderaan **PASS** (alles goed) of **FAIL** (er is iets mis).
Druk op een toets om het venster te sluiten.

## Problemen oplossen

**Mac: "kan niet worden geopend" of "onbekende ontwikkelaar"**
Ga naar Systeeminstellingen → Privacy en beveiliging → scroll omlaag → klik **Toch openen**.
Op een oudere Mac: rechtsklik op het bestand → **Open**. Dit hoeft maar één keer.

**Mac: "geen toegang" of er gebeurt niets bij dubbelklikken**
Soms raakt de ZIP de startrechten kwijt. Open dan één keer de app Terminal, typ `chmod +x ` (met een
spatie erachter), sleep het startbestand in het venster en druk op Enter.

**Windows: "Windows heeft uw pc beschermd"**
Klik op **Meer informatie** en dan **Toch uitvoeren**.

**De browser opent niet**
Kijk in het zwarte venster. Daar staat een adres dat begint met `http://127.0.0.1:`.
Kopieer dat hele adres (met het stuk `?token=` erachter) naar je browser.

## Feedback geven

Vertel per mijlpaal in de chat:
- wat je deed (welke knoppen, welke samples: soort en aantal, geen bestanden sturen);
- wat je hoorde of zag;
- wat je anders wilt.

Bij een fout: maak een schermafbeelding van het zwarte venster en van de browser.
