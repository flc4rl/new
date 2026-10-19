Example curation files for analytics.py (illustration only; the event_id values are made up).

  python analytics.py RESULTS -o OUT --org-codes analytics_codes_example/org_codes.csv
  python analytics.py RESULTS -o OUT --event-codes analytics_codes_example/event_codes.csv

Start from OUT/org_codes_template.xlsx rather than from these files: fill in its
"organisations" sheet and save that sheet as org_codes.csv (UTF-8). event_id values
come from the events_enriched sheet of analytics.xlsx.

org_codes.csv
  include          1 = always in the network, 0 = always left out, blank = by --min-confidence
  merge_into_key   org_key of the organisation this row is a variant of
  canonical_name   name to show
  actor_class      movement_org, ngo, party, union, media, company, state_body, police_court,
                   place, person, project_infrastructure, event_name, reference, site_ui, noise
                   (place, site_ui, noise and event_name are left out of scenario C_curated)
  is_organisation  yes / no / partial (precision coding; gives precision_by_stratum)

event_codes.csv
  series_id        groups editions of a recurring event
  duplicate_of     event_id of the event this row duplicates
  country_override ISO codes separated by ";" (e.g. DE;AT)

Source codes (--source-codes)
  python analytics.py RESULTS -o OUT --source-codes OUT/org_codes_template.xlsx
  The sources_to_code sheet of org_codes_template.xlsx (or that sheet saved as CSV)
  lists every page and PDF with doc_kind (listing, page, programme, report) and a
  suggested class. include 1 keeps a source in the network whatever its doc_kind
  (a programme taken for a report), 0 leaves it out (a report taken for a page);
  blank keeps the classification.

Coding the theme passages (kwic_for_coding.xlsx, column relevance)
  false_match      the match is not the theme at all. Example: "business empire",
                   "media empire" (a company group, not imperialism); the theme
                   file now excludes these senses, but code any that remain this
                   way. (The French verb "empire" (worsens) never occurred in the
                   first full run.)
  off_topic_sense  the word in another sense, e.g. bees "colonising" a meadow.
  list_or_tag      one item of a list of causes or a hashtag.
