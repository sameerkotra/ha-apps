-- Tolls, round 87: group tags together (e.g. "Work") so several regular trips can be tracked as one thing
-- on the Dashboard and Compare, instead of listed one by one. A group is just a name; tags are assigned to
-- it the same way devices are assigned to a car (one group per tag, never required). Deleting a group keeps
-- its tags — they just become ungrouped again, same as deleting a car keeps its devices.

CREATE TABLE toll_pattern_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_toll_pattern_groups_user ON toll_pattern_groups (user_id);

ALTER TABLE toll_patterns ADD COLUMN group_id INTEGER REFERENCES toll_pattern_groups(id) ON DELETE SET NULL;
CREATE INDEX idx_toll_patterns_group ON toll_patterns (group_id);

INSERT INTO schema_version (version) VALUES (13);
