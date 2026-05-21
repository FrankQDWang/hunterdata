# Research Protocol

## Objective

Collect 100 public business contact records for Japan recruitment/headhunter companies across all industries.

## Verification Hierarchy

1. `mhlw_verified`: listed in MHLW `人材サービス総合サイト` under `職業紹介事業`, preferably `有料職業紹介事業`.
2. `association_verified`: listed in JESRA or an official certification directory.
3. `business_keyword_verified`: official website contains recruitment/headhunting keywords.
4. `needs_manual_review`: not accepted into the final 100 until manually resolved.

## Business Keywords

- `人材紹介`
- `職業紹介`
- `転職エージェント`
- `採用支援`
- `ヘッドハンティング`
- `エグゼクティブサーチ`
- `サーチ`
- `スカウト`
- `executive search`
- `headhunting`
- `recruitment`
- `placement`

## Contact Source Rules

Accept:

- Company website contact pages.
- Public company profile pages.
- Public team pages where business email or phone is intentionally published.
- Official association/certification directories.

Reject:

- Login-only pages.
- Paid databases.
- Private LinkedIn/social profile pages.
- CAPTCHA bypasses.
- Inferred email patterns.
- Personal home or non-business contact details.

## Manual Review Notes

If a page requires browser interaction but remains publicly accessible, record:

- Timestamp.
- URL.
- Interaction summary.
- Visible evidence.
- Why it is acceptable.

Store these notes in `data/raw/manual_observations.md`.
