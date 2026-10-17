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
