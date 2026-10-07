# photocraft-psd

Copiado tal cual de https://github.com/storytold/photocraft, `crates/psd`,
commit **47f9306** (2026-10-07). Licencia **MIT OR Apache-2.0** — ver
LICENSE-MIT / LICENSE-APACHE en el repo de origen y NOTICE en este.

## Por que vive aqui y no como dependencia

El crate no esta publicado en crates.io, asi que la alternativa era apuntar a
una revision de git. El proyecto tiene una semana de vida y 365 commits en siete
dias: una fuerza bruta aguas arriba, una rama renombrada o un `git push --force`
nos dejaria sin poder construir, y en el peor momento (un despliegue).

Son 1,2 MB y tres dependencias (`thiserror`, `flate2`, `serde` opcional). El
precio de copiarlo es tener que traer los arreglos a mano; el de no copiarlo es
que la construccion dependa de un repo que todavia se esta moviendo.

## Unico cambio respecto al original

`Cargo.toml`: los valores que heredaba del workspace de photocraft
(`version`, `edition`, `license`, y las versiones de las dependencias) estan
escritos de forma concreta, porque aqui no hay tal workspace. El codigo de
`src/` esta intacto.

## Como traer una version nueva

    git clone --depth 1 --filter=blob:none --sparse https://github.com/storytold/photocraft
    cd photocraft && git sparse-checkout set crates/psd
    # copiar crates/psd aqui, repetir el ajuste del Cargo.toml de arriba,
    # y volver a pasar las pruebas de tests/test_psd_nativo.py
