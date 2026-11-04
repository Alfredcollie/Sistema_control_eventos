# -*- coding: utf-8 -*-
"""Modelos del proyecto de planos (JSON-friendly, sin dependencias)."""
import uuid
from dataclasses import dataclass, field, asdict

UNIDADES = ("m", "cm", "mm")
FACTOR_A_METROS = {"m": 1.0, "cm": 0.01, "mm": 0.001}


def nuevo_id(prefijo="el"):
    return "%s_%s" % (prefijo, uuid.uuid4().hex[:12])


@dataclass
class Capa:
    id: str = field(default_factory=lambda: nuevo_id("cap"))
    nombre: str = "Capa"
    orden: int = 0
    visible: bool = True
    bloqueada: bool = False
    opacidad: float = 1.0

    def a_dict(self):
        return asdict(self)

    @staticmethod
    def desde_dict(d):
        return Capa(id=str(d.get("id") or nuevo_id("cap")),
                    nombre=str(d.get("nombre") or "Capa"),
                    orden=int(d.get("orden") or 0),
                    visible=bool(d.get("visible", True)),
                    bloqueada=bool(d.get("bloqueada", False)),
                    opacidad=float(d.get("opacidad", 1.0)))


@dataclass
class Elemento:
    """Objeto colocado sobre el plano. Las medidas SIEMPRE van en metros."""
    id: str = field(default_factory=lambda: nuevo_id("obj"))
    tipo: str = "rectangulo"          # rectangulo, circulo, texto, cota, imagen...
    nombre: str = ""
    capa: str = ""
    x: float = 0.0                    # centro, en metros
    y: float = 0.0
    ancho: float = 1.0                # metros
    alto: float = 1.0                 # metros
    rotacion: float = 0.0             # grados
    color: str = "#1f538d"
    opacidad: float = 1.0
    visible: bool = True
    bloqueado: bool = False
    orden: int = 0
    texto: str = ""
    tamano_texto: int = 9               # tamaño del nombre en el plano
    texto_pos: str = "abajo"            # abajo | arriba | dentro | izquierda | derecha
    color_texto: str = "#555555"
    extra: dict = field(default_factory=dict)

    def a_dict(self):
        return asdict(self)

    @staticmethod
    def desde_dict(d):
        return Elemento(
            id=str(d.get("id") or nuevo_id("obj")),
            tipo=str(d.get("tipo") or "rectangulo"),
            nombre=str(d.get("nombre") or ""),
            capa=str(d.get("capa") or ""),
            x=float(d.get("x") or 0), y=float(d.get("y") or 0),
            ancho=float(d.get("ancho") or 1), alto=float(d.get("alto") or 1),
            rotacion=float(d.get("rotacion") or 0),
            color=str(d.get("color") or "#1f538d"),
            opacidad=float(d.get("opacidad", 1.0)),
            visible=bool(d.get("visible", True)),
            bloqueado=bool(d.get("bloqueado", False)),
            orden=int(d.get("orden") or 0),
            texto=str(d.get("texto") or ""),
            tamano_texto=int(d.get("tamano_texto") or 9),
            texto_pos=str(d.get("texto_pos") or "abajo"),
            color_texto=str(d.get("color_texto") or "#555555"),
            extra=dict(d.get("extra") or {}))


@dataclass
class Pagina:
    """Página del PDF y su calibración de escala."""
    numero: int = 0
    ancho_pt: float = 0.0
    alto_pt: float = 0.0
    px_por_metro: float = 0.0         # 0 = sin calibrar
    calibrada: bool = False
    puntos_calibracion: list = field(default_factory=list)   # [(x1,y1,x2,y2)]
    distancia_real: float = 0.0       # metros introducidos por el usuario
    ancho_m: float = 0.0              # 📐 hoja en metros (sólo lienzo en blanco)
    alto_m: float = 0.0

    def a_dict(self):
        return asdict(self)

    @staticmethod
    def desde_dict(d):
        return Pagina(numero=int(d.get("numero") or 0),
                      ancho_pt=float(d.get("ancho_pt") or 0),
                      alto_pt=float(d.get("alto_pt") or 0),
                      px_por_metro=float(d.get("px_por_metro") or 0),
                      calibrada=bool(d.get("calibrada", False)),
                      puntos_calibracion=list(d.get("puntos_calibracion") or []),
                      distancia_real=float(d.get("distancia_real") or 0),
                      ancho_m=float(d.get("ancho_m") or 0),
                      alto_m=float(d.get("alto_m") or 0))


def capas_por_defecto():
    """Capas sugeridas (igual que la especificación del módulo)."""
    nombres = [("Plano original", True), ("Arquitectura adicional", False),
               ("Mobiliario", False), ("Escenario y estructuras", False),
               ("Audiovisuales", False), ("Decoración", False),
               ("Circulación", False), ("Textos y cotas", False)]
    return [Capa(id="capa_%d" % (i + 1), nombre=n, orden=i, bloqueada=bloq)
            for i, (n, bloq) in enumerate(nombres)]


@dataclass
class Plano:
    """Proyecto editable de un plano (fuente de verdad: JSON en la nube)."""
    id: str = ""
    cotizacion_id: int = 0
    nombre: str = "Plano"
    descripcion: str = ""
    pdf_ruta: str = ""
    pagina: int = 0                       # índice de la página editada (0 = primera)
    unidad: str = "m"
    version: int = 1
    creacion: str = ""
    modificacion: str = ""
    usuario: str = ""
    paginas: list = field(default_factory=list)
    capas: list = field(default_factory=list)
    elementos: list = field(default_factory=list)
    mascaras: list = field(default_factory=list)
    limpio: bool = False              # ⬜ True = lienzo en blanco (sin PDF)

    def capa(self, capa_id):
        for c in self.capas:
            if c.id == capa_id:
                return c
        return None

    def a_dict(self):
        return {
            "id": self.id, "cotizacion_id": self.cotizacion_id,
            "nombre": self.nombre, "descripcion": self.descripcion,
            "pdf_ruta": self.pdf_ruta, "pagina": self.pagina,
            "unidad": self.unidad, "version": self.version,
            "creacion": self.creacion, "modificacion": self.modificacion,
            "usuario": self.usuario,
            "paginas": [p.a_dict() for p in self.paginas],
            "capas": [c.a_dict() for c in self.capas],
            "elementos": [e.a_dict() for e in self.elementos],
            "mascaras": list(self.mascaras),
            "limpio": bool(self.limpio),
        }

    @staticmethod
    def desde_dict(d):
        plano = Plano(
            id=str(d.get("id") or ""),
            cotizacion_id=int(d.get("cotizacion_id") or 0),
            nombre=str(d.get("nombre") or "Plano"),
            descripcion=str(d.get("descripcion") or ""),
            pdf_ruta=str(d.get("pdf_ruta") or ""),
            pagina=int(d.get("pagina") or 0),
            unidad=str(d.get("unidad") or "m"),
            version=int(d.get("version") or 1),
            creacion=str(d.get("creacion") or ""),
            modificacion=str(d.get("modificacion") or ""),
            usuario=str(d.get("usuario") or ""),
            mascaras=list(d.get("mascaras") or []),
            limpio=bool(d.get("limpio", False)),
        )
        plano.paginas = [Pagina.desde_dict(x) for x in (d.get("paginas") or [])]
        plano.capas = [Capa.desde_dict(x) for x in (d.get("capas") or [])] or capas_por_defecto()
        plano.elementos = [Elemento.desde_dict(x) for x in (d.get("elementos") or [])]
        return plano
