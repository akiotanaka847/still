//! Reescribe un PSD conservandolo entero.
//!
//! Still lee los PSD con psd-tools, que es quien entiende la EngineData de las
//! capas de texto y los objetos inteligentes —de ahi sale toda la inteligencia
//! del convertidor—. Lo que psd-tools NO sabe hacer es volver a escribir el
//! archivo, asi que hasta ahora el PSD que entregabamos se construia de cero:
//! salia plano, en RGB y con 21 capas de pixeles, perdiendo los 12 grupos, los
//! 10 objetos inteligentes y las 11 capas de texto del original.
//!
//! Esto hace lo contrario: abre el archivo del cliente y toca solo lo que se le
//! pide. Todo lo demas —modo de color, perfil ICC, mascaras, efectos, bloques
//! que ni siquiera entiende— vuelve a salir byte por byte.
//!
//! Las capas se identifican por su `lyid`, el identificador que Photoshop le da
//! a cada capa: es estable, unico, y lo leen igual psd-tools y esta libreria.
//! Emparejar por nombre o por posicion se rompe en cuanto hay dos capas que se
//! llaman igual, que en una plantilla de punto de venta es lo normal.

use photocraft_psd::{PsdFile, TaggedBlock};
use std::collections::HashMap;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let r = match args.first().map(String::as_str) {
        Some("renombrar") if args.len() == 4 => renombrar(&args[1], &args[2], &args[3]),
        Some("inspeccionar") if args.len() == 2 => inspeccionar(&args[1]),
        _ => {
            eprintln!(
                "uso:\n  \
                 still-psd renombrar <entrada.psd> <mapa.tsv> <salida.psd>\n  \
                 still-psd inspeccionar <entrada.psd>\n\n\
                 mapa.tsv: una linea por capa, «<lyid>\\t<nombre>»"
            );
            return ExitCode::from(2);
        }
    };
    match r {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::FAILURE
        }
    }
}

type R = Result<(), Box<dyn std::error::Error>>;

/// Una linea por capa: identificador, tipo, nombre. Es lo que deja comprobar
/// desde fuera que el archivo escrito conserva lo que tenia que conservar.
fn inspeccionar(entrada: &str) -> R {
    let psd = PsdFile::from_bytes(&std::fs::read(entrada)?)?;
    println!("modo\t{:?}", psd.header.color_mode);
    println!("lienzo\t{}\t{}", psd.header.width, psd.header.height);
    println!("icc\t{}", psd.icc_profile().is_some());
    for l in psd.layers() {
        println!(
            "capa\t{}\t{}\t{}",
            l.layer_id().map_or_else(|| "-".into(), |v| v.to_string()),
            clase(l),
            l.name()
        );
    }
    Ok(())
}

fn clase(l: &photocraft_psd::LayerRecord) -> &'static str {
    if l.block(b"TySh").is_some() || l.block(b"tySh").is_some() {
        "texto"
    } else if [b"SoLd", b"PlLd", b"SoLE", b"lnk2"].iter().any(|k| l.block(*k).is_some()) {
        "objeto"
    } else if l.section_divider().is_some() {
        "grupo"
    } else {
        "pixel"
    }
}

fn renombrar(entrada: &str, mapa: &str, salida: &str) -> R {
    let nuevos = leer_mapa(&std::fs::read_to_string(mapa)?)?;
    let crudo = std::fs::read(entrada)?;
    let mut psd = PsdFile::from_bytes(&crudo)?;

    let antes = huella(&psd);
    let mut hechos = 0usize;

    for l in psd.layers_mut() {
        let Some(id) = l.layer_id() else { continue };
        let Some(nuevo) = nuevos.get(&id) else { continue };

        // El nombre vive en dos sitios y hay que cambiar los dos: el campo
        // heredado (Pascal, ASCII) y el bloque `luni` en Unicode, que es el que
        // Photoshop enseña de verdad. Tocar solo uno deja el archivo diciendo
        // dos cosas distintas segun quien lo lea.
        l.name = nuevo.as_bytes().to_vec();
        let luni = TaggedBlock::unicode_name(nuevo);
        match l.blocks.iter_mut().find(|b| &b.key == b"luni") {
            Some(b) => *b = luni,
            None => l.blocks.push(luni),
        }
        hechos += 1;
    }

    let bytes = psd.to_bytes()?;
    std::fs::write(salida, &bytes)?;

    // Releer lo escrito y comparar la huella. Un PSD que no se puede volver a
    // abrir, o que perdio una capa de texto por el camino, no sirve de nada y
    // es mejor saberlo aqui que en la imprenta.
    let releido = PsdFile::from_bytes(&std::fs::read(salida)?)?;
    let despues = huella(&releido);
    if antes != despues {
        return Err(format!(
            "la estructura cambio al escribir: antes {antes:?}, despues {despues:?}"
        )
        .into());
    }

    println!("renombradas\t{hechos}");
    println!("capas\t{}", despues.0);
    println!("texto\t{}", despues.1);
    println!("objetos\t{}", despues.2);
    println!("grupos\t{}", despues.3);
    Ok(())
}

/// (capas, de texto, objetos inteligentes, grupos). Lo que tiene que sobrevivir.
fn huella(p: &PsdFile) -> (usize, usize, usize, usize) {
    let mut t = 0;
    let mut o = 0;
    let mut g = 0;
    for l in p.layers() {
        match clase(l) {
            "texto" => t += 1,
            "objeto" => o += 1,
            "grupo" => g += 1,
            _ => {}
        }
    }
    (p.layers().len(), t, o, g)
}

fn leer_mapa(texto: &str) -> Result<HashMap<u32, String>, String> {
    let mut m = HashMap::new();
    for (n, linea) in texto.lines().enumerate() {
        let linea = linea.trim_end_matches('\r');
        if linea.is_empty() {
            continue;
        }
        let Some((id, nombre)) = linea.split_once('\t') else {
            return Err(format!("linea {}: falta el tabulador", n + 1));
        };
        let id: u32 = id.trim().parse().map_err(|_| format!("linea {}: «{id}» no es un lyid", n + 1))?;
        if nombre.is_empty() {
            return Err(format!("linea {}: nombre vacio", n + 1));
        }
        m.insert(id, nombre.to_string());
    }
    Ok(m)
}
