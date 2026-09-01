# Changelog

## [1.4.0](https://github.com/ispy4you/auto-telegram-news/compare/v1.3.0...v1.4.0) (2026-09-01)


### Features

* add a per-setting reset to default ([#30](https://github.com/ispy4you/auto-telegram-news/issues/30)) ([a1ab7bd](https://github.com/ispy4you/auto-telegram-news/commit/a1ab7bd4ef258ae5deefb58b3936a23c2f1fa8bd))
* fetch lost media when the post page opens ([#33](https://github.com/ispy4you/auto-telegram-news/issues/33)) ([5eed828](https://github.com/ispy4you/auto-telegram-news/commit/5eed8287f7b62b2b63fec188864516edda56da1e))
* full Telegram formatting in the post editor ([#35](https://github.com/ispy4you/auto-telegram-news/issues/35)) ([00d8781](https://github.com/ispy4you/auto-telegram-news/commit/00d8781f7fa9935239b7d538a5fd4a339c0bed8f))
* let a post be published as a Telegram quote ([#32](https://github.com/ispy4you/auto-telegram-news/issues/32)) ([f729867](https://github.com/ispy4you/auto-telegram-news/commit/f72986754cf0b96aee35dc55f94acf254e55de2c))
* make the Telegram login understandable ([#27](https://github.com/ispy4you/auto-telegram-news/issues/27)) ([01ccaf4](https://github.com/ispy4you/auto-telegram-news/commit/01ccaf4ba4c6ced9011a02494cf543fb06cd0d4d))
* вкладка «Генерация» — пост из текста, вставленного руками ([#40](https://github.com/ispy4you/auto-telegram-news/issues/40)) ([b99a2af](https://github.com/ispy4you/auto-telegram-news/commit/b99a2af4030d7800db5f4af6c15150c020338e5d))
* одно поле промпта вместо двух + кнопка проверки ([#37](https://github.com/ispy4you/auto-telegram-news/issues/37)) ([747462f](https://github.com/ispy4you/auto-telegram-news/commit/747462f37fb82256af8b5172711cea1f41b2a066))
* показать скрытую часть промпта, без права редактировать ([#39](https://github.com/ispy4you/auto-telegram-news/issues/39)) ([28076c1](https://github.com/ispy4you/auto-telegram-news/commit/28076c14f5e749548a4b329ecf7447505eb776b8))
* своё фото и видео в посте ([#41](https://github.com/ispy4you/auto-telegram-news/issues/41)) ([a6a6922](https://github.com/ispy4you/auto-telegram-news/commit/a6a6922e42949a8e7bc8ed4e7f31488fa4bb52f6))


### Bug Fixes

* allow deleting a post that a duplicate points at ([#34](https://github.com/ispy4you/auto-telegram-news/issues/34)) ([fd9e493](https://github.com/ispy4you/auto-telegram-news/commit/fd9e4939262813975e8475d6abad58a85d77da68))
* name the account without a re-login, show the banner only on connect ([#29](https://github.com/ispy4you/auto-telegram-news/issues/29)) ([9ebca99](https://github.com/ispy4you/auto-telegram-news/commit/9ebca990be59bd0dfe524a6d466d786a3a9442ef))
* say why the model returned nothing ([#36](https://github.com/ispy4you/auto-telegram-news/issues/36)) ([e55579a](https://github.com/ispy4you/auto-telegram-news/commit/e55579a6ca018b25bbee17530e78139cb3f868df))
* stop curly braces in the prompt from breaking generation ([#31](https://github.com/ispy4you/auto-telegram-news/issues/31)) ([245c6a2](https://github.com/ispy4you/auto-telegram-news/commit/245c6a2fe52320f871758d6bc16b18c97b263859))

## [1.3.0](https://github.com/ispy4you/auto-telegram-news/compare/v1.2.1...v1.3.0) (2026-08-25)


### Features

* enable semantic deduplication by default ([#20](https://github.com/ispy4you/auto-telegram-news/issues/20)) ([26908b9](https://github.com/ispy4you/auto-telegram-news/commit/26908b913daff116b3d80be50e44a4c5918b1234))
* manage the database schema with Alembic ([#16](https://github.com/ispy4you/auto-telegram-news/issues/16)) ([d27b2af](https://github.com/ispy4you/auto-telegram-news/commit/d27b2af28c32976f6baa701451518744d247b969))
* prune the action log and stop logging empty scheduler runs ([#19](https://github.com/ispy4you/auto-telegram-news/issues/19)) ([0cb1c88](https://github.com/ispy4you/auto-telegram-news/commit/0cb1c888c4bad242505abdb1ad212a133f884861))
* refetch missing media from the source before publishing ([#21](https://github.com/ispy4you/auto-telegram-news/issues/21)) ([39637d1](https://github.com/ispy4you/auto-telegram-news/commit/39637d1d64bea2670051f3f474940fce7707d67d))
* store the Telethon session in the database ([#11](https://github.com/ispy4you/auto-telegram-news/issues/11)) ([d52f093](https://github.com/ispy4you/auto-telegram-news/commit/d52f0935d47df3016ef0c5db0c43feb378a60e64))


### Bug Fixes

* accept non-ASCII passwords, and cover login and CSRF with tests ([#24](https://github.com/ispy4you/auto-telegram-news/issues/24)) ([ac9bbcb](https://github.com/ispy4you/auto-telegram-news/commit/ac9bbcb9422016abeb08fa11e77704e8e0bc4f40))
* **docker:** force apt over IPv4 ([#14](https://github.com/ispy4you/auto-telegram-news/issues/14)) ([1a4f132](https://github.com/ispy4you/auto-telegram-news/commit/1a4f132be27275825104149640112bc9008d269c))
* **docker:** retry apt downloads instead of failing the build ([#13](https://github.com/ispy4you/auto-telegram-news/issues/13)) ([e46c0f5](https://github.com/ispy4you/auto-telegram-news/commit/e46c0f57339de1be7fc866e473c290e2b12aac3c))
* publish to every target channel, not only the first ([#25](https://github.com/ispy4you/auto-telegram-news/issues/25)) ([4c64bc1](https://github.com/ispy4you/auto-telegram-news/commit/4c64bc194b17ebf51eedb09a8d0e2bd797f75dc3))
* reuse the listener's Telethon client when restoring media ([#26](https://github.com/ispy4you/auto-telegram-news/issues/26)) ([9437f31](https://github.com/ispy4you/auto-telegram-news/commit/9437f317367035f75e7d898c29bd686cd7c0ec98))
* stop duplicate publications and make the retry limit work ([#15](https://github.com/ispy4you/auto-telegram-news/issues/15)) ([f5fbe12](https://github.com/ispy4you/auto-telegram-news/commit/f5fbe1211bba3488c9cef8b2aea899d3d443d07e))

## [1.2.1](https://github.com/ispy4you/auto-telegram-news/compare/v1.2.0...v1.2.1) (2026-08-24)


### Bug Fixes

* make the container healthcheck work on App Platform ([#9](https://github.com/ispy4you/auto-telegram-news/issues/9)) ([02003f5](https://github.com/ispy4you/auto-telegram-news/commit/02003f57468d14beeac3ddda6e0e1f40bce954c0))

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
