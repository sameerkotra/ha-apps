-- A web address per account (REQUIREMENTS.md section 11): the bank's login or statements page,
-- shown as a link on Setup → Accounts, the account page and Transactions → Accounts.
ALTER TABLE accounts ADD COLUMN website_url TEXT;

INSERT INTO schema_version (version) VALUES (20);
