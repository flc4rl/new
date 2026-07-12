"""
Conceptual taxonomy for the departmental corpus frame analysis.

Structure
---------
Two levels, matching the methodology described in the study:

  FRAME (e.g. "colonialism")
      -> CONCEPT (e.g. "settler_colonialism")
          -> VARIANTS: regex fragments matching the surface forms of that
             concept (plural/verb/noun inflections etc.)

"Conceptual breadth" (per document) is counted at the CONCEPT level: a
document that mentions both "colonialism" and "coloniality" contributes 2
to its breadth score, even though both roll up into the same "colonialism"
frame. "Frame dominance" (corpus level and per document) is counted at the
FRAME level: all concept hits belonging to a frame are summed before the
proportions are computed. This is what allows the corpus to show a small
number of documents engaging 6+ *concepts* while the dominance chart still
only ever shows 4 named *frames*.

Only substantive/analytic word forms are included (nouns and verbs that
signal the term is being used as a category of analysis: "colonialism",
"racialization", "settler colonialism" ...). Purely descriptive or
period-marking adjectives ("colonial architecture", "the imperial court",
"post-war") are deliberately left out of the pattern set so that
incidental, non-theoretical usage is not counted as conceptual engagement.
Tune EXCLUDE_CONTEXT below if your corpus needs additional guardrails.
"""

from __future__ import annotations

from typing import Dict, List

# Each concept maps to a list of regex fragments (no leading/trailing \b --
# those are added automatically). Fragments are matched case-insensitively
# against the cleaned document text.
TAXONOMY: Dict[str, Dict[str, List[str]]] = {
    "colonialism": {
        "colonialism": [r"colonialis(?:m|t|ts)"],
        "coloniality": [r"colonialit(?:y|ies)"],
        "colonization": [r"coloniz(?:e|ed|es|ing|ation|ations)", r"colonis(?:e|ed|es|ing|ation|ations)"],
        "settler_colonialism": [r"settler[\s-]colonial(?:ism)?"],
        "decoloniality": [r"decoloni(?:al|ality|zation|sation|ze|se|zing|sing)"],
    },
    "imperialism": {
        "imperialism": [r"imperialis(?:m|t|ts)"],
        "empire": [r"empires?"],
        "neo_imperialism": [r"neo[\s-]imperialis(?:m|t|ts)"],
    },
    "racism": {
        "racism": [r"racism"],
        "racialization": [r"racializ(?:e|ed|es|ing|ation|ations)", r"racialis(?:e|ed|es|ing|ation|ations)"],
        "white_supremacy": [r"white[\s-]supremac(?:y|ist|ists)"],
        "racial_capitalism": [r"racial[\s-]capitalis(?:m|t|ts)"],
    },
    "fascism": {
        "fascism": [r"fascis(?:m|t|ts)"],
        "authoritarianism": [r"authoritarianis(?:m|t|ts)"],
        "totalitarianism": [r"totalitarianis(?:m|t|ts)"],
    },
}

# Frame display order — used consistently across the breadth histogram,
# the dominance bar chart, and the per-document proportion columns so that
# each frame keeps the same identity/color everywhere it appears.
FRAME_ORDER: List[str] = ["colonialism", "imperialism", "racism", "fascism"]

# Optional: bigrams/phrases whose presence right around a hit should
# *discount* that hit as descriptive/background use rather than analytic
# engagement (e.g. a work merely dated to "the colonial period" or naming
# a research center). Matched as a case-insensitive substring within a
# small window around each raw hit; see `is_descriptive_context` in
# analyze_corpus.py. Leave empty to disable and count every raw hit.
EXCLUDE_CONTEXT: List[str] = [
    "colonial period",
    "colonial era",
    "colonial architecture",
    "imperial court",
    "imperial college",
    "imperial units",
    "imperial measurements",
]


def all_concepts() -> List[str]:
    return [c for frame in FRAME_ORDER for c in TAXONOMY[frame]]


def concept_to_frame() -> Dict[str, str]:
    return {c: frame for frame in FRAME_ORDER for c in TAXONOMY[frame]}
