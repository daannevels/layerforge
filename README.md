# Layerforge

Layerforge maakt **nieuwe drum one-shots** uit 2 tot 4 van je eigen samples van hetzelfde soort
(kick, clap, crash, open hat, closed hat of perc). Je kiest per sample welk stuk je mooi vindt,
bijvoorbeeld de body van kick 1 en de top van kick 2. Layerforge mengt die stukken tot nieuwe samples.
Alles draait op je eigen computer. Er gaat niets naar internet.

> **Status: mijlpaal M2.** Je kunt 2 tot 4 samples laden, per sample kiezen wat je wilt houden,
> en één blend maken. Meerdere variaties komen in mijlpaal M3.

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

1. Kies bovenaan het **Sound type** (kick, clap, closedhat, openhat, crash of perc).
2. Sleep een sample in een vak, of klik op het vak om een bestand te kiezen. Laad er 2 tot 4.
   Met **+ Add sample slot** maak je een extra vak. Met **✕** haal je een sample weg.
3. Onder elk sample staat een raster. De rijen zijn **banden** (stukken van het frequentiebereik,
   van Sub onderaan tot Air bovenaan). De kolommen zijn **tijdzones**: Attack (de klap aan het begin),
   Sustain (het lijf) en Tail (de staart).
4. Klik op een vakje: **open** (leeg) → **KEEP** (oranje: dit stuk wil ik) → **✕ avoid** (dit stuk wil ik niet)
   → weer open. Klik op een rijnaam (bijv. *Body*) of kolomnaam (bijv. *Attack*) om de hele rij of kolom
   in één keer te zetten.
5. Klik **Generate**. Onder **Result** verschijnt je nieuwe sample.
6. Klik op de golfvorm of ▶ om te luisteren. **Compare with simple stack** wisselt bij elke klik tussen jouw
   blend en de "simpele stapel" (alle samples gewoon op elkaar). Alles speelt even luid af.
   **Download** slaat de WAV op. Spatiebalk = afspelen of stoppen.

Open vakjes vult Layerforge zelf in: daar komt het sample met de meeste energie in dat vakje.
In de laagste banden (Sub en Body bij een kick) mengt Layerforge nooit twee samples, want lage tonen
kunnen elkaar uitdoven. Daar wint altijd één sample.

**Waar staan mijn bestanden?** In de map `Layerforge` in je persoonlijke map
(Mac: `/Users/<jouw naam>/Layerforge`, Windows: `C:\Users\<jouw naam>\Layerforge`).
Elke keer dat je op Generate klikt, komt er een map bij in `Projects`. Daarin staan een kopie van je
samples (`inputs`), de blend (`variations/v01.wav`), het recept (`v01.json`, alle getallen van de blend)
en `simple_stack.wav`.

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
