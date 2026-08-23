# Changelog

## [1.2.0](https://github.com/ispy4you/auto-telegram-news/compare/v1.1.0...v1.2.0) (2026-08-23)


### Features

* support deployment on Timeweb App Platform ([#7](https://github.com/ispy4you/auto-telegram-news/issues/7)) ([eb914db](https://github.com/ispy4you/auto-telegram-news/commit/eb914dbb449ae37068283d6f774f7fe87f193192))

## [1.1.0](https://github.com/ispy4you/auto-telegram-news/compare/v1.0.0...v1.1.0) (2026-08-09)


### Features

* add Docker/docker-compose for one-command setup ([437035d](https://github.com/ispy4you/auto-telegram-news/commit/437035d0b5ccfd199236fc3d37fd8f8b4537381d))
* add web-based Telegram login, no terminal required ([ae07756](https://github.com/ispy4you/auto-telegram-news/commit/ae077568590ad1e9b61966e37800776a6f4fc3f1))
* move most .env settings into the web UI, redesign Settings page ([#5](https://github.com/ispy4you/auto-telegram-news/issues/5)) ([f17d585](https://github.com/ispy4you/auto-telegram-news/commit/f17d58588376c3940f4f43d1a16bafe7046e94ec))
* skip posts older than a configurable age when collecting ([7c7692d](https://github.com/ispy4you/auto-telegram-news/commit/7c7692db91508e8b5056458f6bf9027150ef80b7))


### Bug Fixes

* correct published post timestamps and per-channel stats ([#4](https://github.com/ispy4you/auto-telegram-news/issues/4)) ([87d9499](https://github.com/ispy4you/auto-telegram-news/commit/87d949937c2ecdfbf801533568e677ec9f350bbc))
* make server logs actually readable ([fe5f970](https://github.com/ispy4you/auto-telegram-news/commit/fe5f970ff5dbe11c8e093daca45370a3020e8051))
* show clear connected state in Telegram account settings ([6121ff7](https://github.com/ispy4you/auto-telegram-news/commit/6121ff71acc72e34de348ac3706cce3b0e899727))
* wire duplicate_threshold setting into the dedup service ([5d5210f](https://github.com/ispy4you/auto-telegram-news/commit/5d5210fee91566f868b2aab4bf94114cf86d863d))


### Documentation

* document required PR + squash-merge workflow ([#3](https://github.com/ispy4you/auto-telegram-news/issues/3)) ([c5904bc](https://github.com/ispy4you/auto-telegram-news/commit/c5904bc276deb2eb6a34e56495dedf72941f9c2d))

## 1.0.0 (2026-08-09)


### Features

* add automated releases and changelog via release-please ([8469712](https://github.com/ispy4you/auto-telegram-news/commit/8469712fea1b96d31a00a5c84d16b8849678c0c8))
