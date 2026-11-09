# 3MF Workspace (fork)

Editor web dei progetti 3MF di OrcaSlicer e Bambu Studio dentro Printbuddy: si apre un 3MF dal File
Manager ("Modifica 3MF"), si cambiano le impostazioni del progetto, si salva una copia, si fa lo slicing
della copia sui sidecar esistenti, si vede il G-code e si stampa con il flusso di stampa esistente.
Nessun motore di slicing nuovo, nessun container nuovo.

## Architettura

```
FileManagerPage ("Modifica 3MF")
  └─ /files/:id/workspace  →  farm/workspace/WorkspacePage.tsx (schermo intero)
        ├─ ProjectTree.tsx      piatti (miniature) e oggetti, filamento, impostazioni per oggetto
        ├─ ModelViewer.tsx      geometria (componente esistente)
        ├─ GcodeViewer.tsx      anteprima del risultato (componente esistente)
        ├─ SettingsPanel.tsx    schede dello slicer, ricerca, originale/modificato, ripristino
        │    └─ SettingControl.tsx   intero, decimale, booleano, opzioni, un valore per filamento/estrusore
        ├─ draft.ts             modifiche non salvate e controlli lato browser
        └─ PrintModal           stampa (componente esistente)

GET  /api/v1/farm/workspace/files/{id}       farm/workspace/routes.py → project.py + settings.py
POST /api/v1/farm/workspace/files/{id}/copy  idem: copia nuova nella libreria interna
GET  /api/v1/farm/library/files/{id}/project slicer, sidecar, preset (esistente; ora anche per file fuori dal NAS)
POST /api/v1/farm/library/files/{id}/slice   slicing con le impostazioni del 3MF (esistente; idem)
```

- `project.py`: lettura sicura dell'archivio (dimensione, numero di voci, ZIP bomb, Zip Slip, voci
  duplicate, JSON e XML malformati), struttura da `model_settings.config` (piatti, istanze, oggetti, parti,
  impostazioni per oggetto), tipo di file (progetto, `.gcode.3mf`, CAD, slicer senza sidecar) e scrittura
  della copia.
- `settings.py`: catalogo delle impostazioni dagli schemi già nel repo
  (`backend/app/data/{process,filament,printer}_fields.json`, usati dall'editor dei preset cloud),
  categorie mappate sulle schede dello slicer, regole di modifica e controllo di ogni valore.

## Decisioni

- **L'originale non si scrive mai.** La copia è un `LibraryFile` nuovo nella libreria interna
  (`source_type = "3mf_workspace"`, `file_metadata.workspace` con originale, sha della versione di
  partenza, modifiche). Va nella cartella dell'originale se è interna, altrimenti nella radice: mai accanto
  a un originale NAS, dove la sincronizzazione la prenderebbe per un progetto della farm (stato, piatti,
  prodotti, reslice).
- **Modifica mirata.** Cambia solo `Metadata/project_settings.config`. Tutte le altre voci (geometrie,
  componenti, trasformazioni, piatti, impostazioni per oggetto, assegnazioni dei filamenti, pittura di
  supporti e cucitura, modificatori, miniature, `slice_info.config`, metadati sconosciuti) restano con
  nome, ordine, data, compressione e byte. La copia si rilegge e si confronta voce per voce (CRC e
  dimensione) prima di salvarla; la riga in libreria nasce solo dopo.
- **Formato del progetto.** I valori restano stringhe o liste per filamento/estrusore come li scrive lo
  slicer; `15%` resta percentuale, `1`/`0` e `true`/`false` restano tali, `nil` (ereditato) non si scrive a
  mano. Una chiave cambiata si aggiunge a `different_settings_to_system` della sua sezione, così lo slicer
  la mostra come modificata rispetto al preset di sistema.
- **Cosa si può cambiare.** Solo tipi con un controllo affidabile (intero, decimale con min/max dello
  schema, booleano, opzioni) e solo categorie che non toccano la compatibilità con la stampante: tutto il
  processo; del filamento temperatura, flusso, raffreddamento, camera, retrazione; della stampante solo la
  retrazione. Identificatori e nomi dei preset, testo e G-code, tipo/densità/diametro del materiale, piatto,
  ugello e limiti macchina restano in sola lettura. Le chiavi che lo schema non conosce si mostrano come
  sono, in sola lettura, senza categoria inventata.
- **Preset ed ereditarietà.** Il valore mostrato è quello salvato nel progetto. Il file dice se differisce
  dal preset di sistema (`different_settings_to_system`); il valore del preset di sistema non è nel file e
  non si indovina. Gli oggetti con un valore proprio (in `model_settings.config`) sono indicati: lo slicer
  usa quello per l'oggetto.
- **Concorrenza.** La pagina riceve lo sha256 del file letto; il salvataggio rilegge il file e rifiuta (409)
  se è cambiato. Un file NAS si legge dal NAS, mai dalla copia in cache.
- **Slicing delle copie.** `farmProjectApi.get()` e lo slice con le impostazioni del progetto chiedevano lo
  stato `FarmLibraryFile`, che esiste solo per i file delle cartelle sincronizzate. Per un 3MF fuori da
  quelle cartelle (copia o upload) lo stato ora si legge dal file a ogni richiesta e non si salva: nessuno
  stato fittizio da tenere aggiornato. Le copie quindi non hanno piatti della farm, non entrano nei prodotti,
  nel controllo problemi della libreria (che legge solo i 3MF di prodotti attivi) né nel reslice automatico
  (che parte solo dalla sincronizzazione). Si slicano solo su richiesta. In coda, il controllo "slice di
  una versione vecchia" (`jobs.service._slice_reason`) non ha una versione della farm da seguire per una
  sorgente interna senza stato: l'output (o il G-code estratto per Klipper) si stampa com'è stato slicato.
- **Salvataggio.** Una copia alla volta (la sorgente sta in memoria, fino a 128 MB), fuori dall'event loop:
  la copia si scrive in un file temporaneo, si verifica, si rinomina, e il file e la sua riga in libreria
  (con la provenienza) nascono insieme; se qualcosa fallisce non resta niente. Una voce danneggiata o
  cifrata dà 422.

## Layout come i client (OrcaSlicer / Bambu Studio)

Disposizione, impostazioni, testi italiani, icone e colori vengono da OrcaSlicer `v2.5.0` (AGPL-3.0 come
Printbuddy; vedi `THIRD_PARTY_NOTICES.md`). `tools/orca_settings/generate.py` legge da un checkout del tag
`Tab.cpp` (pagine, gruppi e ordine), `PrintConfig.cpp` (etichette, tooltip, unità, intervalli, opzioni,
modalità), `PublishSettings.cpp` e `OrcaSlicer_it.po`, e scrive `frontend/src/farm/workspace/orca-assets/`
(`settings-layout.json` e le icone copiate da `resources/images`, mai loghi). I dati di OrcaSlicer guidano
solo disposizione e testi: cosa si può modificare e i controlli restano quelli di Printbuddy. Il tema scuro
del workspace usa la palette scura di OrcaSlicer (`StateColor.cpp`). La pagina non usa il nome OrcaSlicer o
Bambu Studio come marchio: compare solo come slicer che ha salvato il progetto.

- **Barra in alto:** Prepara / Anteprima, Salva copia, Slice del piatto, Stampa, Esporta (in Prepara il 3MF,
  in Anteprima il `.gcode.3mf` del piatto).
- **Barra laterale:** stampante (preset, ugello, piatto di stampa) e filamenti con colore; ognuno ha il
  pulsante che apre le sue impostazioni. Poi gli oggetti (del piatto scelto) con le impostazioni proprie in
  sola lettura, e il processo con le schede dei client (Qualità, Resistenza, Velocità, Supporti,
  Multimateriale, Altro, con le loro icone) e l'interruttore Semplice / Avanzate; le impostazioni di
  stampante e filamento si aprono con le pagine di OrcaSlicer.
- **Semplice / Avanzate:** la modalità di ogni opzione in OrcaSlicer (`def->mode`; Semplice quando non ne
  dichiara un'altra). Una chiave modificata resta visibile anche in Semplice.
- **Centro (Prepara):** vista 3D, barra dei piatti numerati in basso, strumenti oggetto (sposta, ruota,
  scala, taglia, pittura supporti, pittura cucitura) con le icone di OrcaSlicer, visibili ma disattivati
  fino alla fase 2.
- **Anteprima:** il G-code del piatto nel `GcodeViewer` esistente, in modalità "slicer": cursore verticale
  dei layer, cursore orizzontale delle mosse del layer in cima (le mosse oltre il cursore non si
  disegnano), legenda per tipo di elemento, tempo e filamento per colore (da `slice_info.config`).
  `gcode-preview` colora per estrusore e legge solo T0-T7, quindi i tipi di elemento dei client sono
  raggruppati in 8 gruppi (`utils/gcodeFeatures.ts`). Si apre l'ultimo slice del piatto già salvato
  (`outputs` del documento), anche da una sessione precedente; uno slice di una versione vecchia del 3MF
  è segnalato e non si stampa da qui.

## Roadmap: editor 3D (fase successiva, non in questa PR)

Preparato in questa PR: la struttura del progetto arriva dal backend con l'identità delle istanze
(`plates[].instances`: `object_id` + `instance_id`), oggetti, parti, filamento e impostazioni per oggetto;
la scrittura della copia è già "cambia solo le voci necessarie, verifica il resto byte per byte".
`ModelViewer` non ricarica più il file a ogni render quando manca `buildVolume`.

Proposta, in passi verificabili uno alla volta:

1. **Separare `ModelViewer`** in `parse3mf.ts` (geometria, componenti, build item con id e trasformazione),
   `sceneGraph.ts` (oggetto → istanze → mesh, piatto) e il componente di rendering. Il parser tiene
   `object_id`/`instance_id` per ogni mesh (oggi le unisce per estrusore).
2. **Selezione** di oggetti e istanze nella vista (raycast) collegata all'albero a sinistra; nessuna
   scrittura.
3. **Modello delle modifiche geometriche** nel browser: trasformazione per istanza (sposta, ruota, scala),
   duplica, assegna a un piatto, filamento dell'oggetto o della parte. Sono operazioni su
   `3D/3dmodel.model` (`<item transform>`, `<component transform>`) e su `model_settings.config`
   (`model_instance`, `extruder`, piatti); i mesh non cambiano.
4. **Serializzazione verificata** prima di qualunque salvataggio: per ogni operazione, test di andata e
   ritorno su progetti reali di Orca e Bambu (apertura nello slicer GUI e slice CLI identico salvo la
   modifica). Da curare: matrici di Bambu (trasformazione dell'item × componente), offset dei piatti nelle
   coordinate globali, `assemble` e `identify_id`, aggiornamento di `plate_N.json` e miniature
   (rigenerate dallo slicer al prossimo slice).
5. **Salvataggio come copia** con lo stesso controllo di integrità di oggi, esteso alle voci geometriche
   modificate. Modificatori geometrici e pittura restano in sola lettura finché non c'è un formato
   verificato.
