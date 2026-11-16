# A lyrics adapter, later

This is a plan for a trained adapter. Nothing here is trained, and this change does not start a run.

## What it would be

A small adapter on the music half, aimed at the words rather than at a style or a voice. The model would be asked to keep the lyric line it was given, in order, for the section it is in. RS-TW and RS-FW would each have their own copy: the two backbones do not share a lyric pull, which is why their `cfg_lyrics` defaults already differ (`lab/cfg_lyrics_sweep.py`: RS-TW 2.4, RS-FW 2.2).

## What would be measured

Word error, the same number the lyrics gate uses (`lab/gloss_core.py`). A held-out set of songs, a few seeds each, WER before and after, and a listen for the failure the gate cannot see: a line sung clearly and still the wrong line. The meter check (`lab/meter_retry.py`) would stay, because an adapter does not know how many notes a phrase was given.

## What stands in until then

- The lyrics gate reseeds while the word error is over a line.
- `cfg_lyrics` is set per backbone, and a sweep can report the error across the grid without training anything.
- A section whose syllables and vocal notes disagree is sung again. A fault of the whole take (the VAE's buzz, clipping) is still Debuzz or Remaster, not another section.

## What this plan does not do

It does not pick a rank, a corpus, or a trainer. Those belong to the run that trains it. Diction adapters in the trainer stay a separate item: they learn a speaker, not the line.
