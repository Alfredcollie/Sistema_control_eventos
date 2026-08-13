# -*- coding: utf-8 -*-
"""
BUFFER_MEMORIA.PY (Caché Inteligente con TTL y Limpieza Automática)
Almacena datos temporalmente en la memoria RAM para evitar consultas repetitivas
a Supabase. Los datos expiran automáticamente después de un tiempo definido.
"""
import time
import threading

class CacheInteligente:
    def __init__(self):
        self.almacen = {}
        # Tiempo de vida por defecto del caché: 300 segundos (5 minutos)
        self.ttl_segundos = 300 
        self.modo_lectura = False
        self._ciclo_activo = False

    def guardar(self, clave, datos, ttl_personalizado=None):
        """Guarda un dato en memoria con su marca de tiempo actual."""
        tiempo_expiracion = ttl_personalizado if ttl_personalizado else self.ttl_segundos
        self.almacen[clave] = {
            'contenido': datos,
            'timestamp': time.time(),
            'ttl': tiempo_expiracion
        }

    def obtener(self, clave):
        """Devuelve el dato solo si no ha caducado. Si caducó, lo borra y devuelve None."""
        if clave in self.almacen:
            registro = self.almacen[clave]
            # Si estamos en modo lectura offline, los datos nunca caducan
            if self.modo_lectura:
                return registro['contenido']
                
            edad_dato = time.time() - registro['timestamp']
            if edad_dato < registro['ttl']:
                return registro['contenido']
            else:
                # El dato es demasiado viejo, lo eliminamos
                del self.almacen[clave]
        return None

    def invalidar(self, clave=None):
        """Borra un dato específico del caché o vacía todo."""
        if clave:
            if clave in self.almacen:
                del self.almacen[clave]
        else:
            self.almacen.clear()

    def cargar_copia_local(self):
        """Función de compatibilidad para el modo offline de control_general.py"""
        pass

    def iniciar_ciclo(self):
        """
        Función requerida por control_general.py al iniciar sesión.
        Inicia un ciclo silencioso en segundo plano para limpiar la memoria caducada.
        """
        if not self._ciclo_activo:
            self._ciclo_activo = True
            hilo = threading.Thread(target=self._limpieza_automatica, daemon=True)
            hilo.start()
            
    def _limpieza_automatica(self):
        """Revisa cada 60 segundos y elimina de la RAM los datos que ya caducaron."""
        while self._ciclo_activo:
            time.sleep(60)
            if self.modo_lectura:
                continue
                
            ahora = time.time()
            claves_a_borrar = []
            for clave, registro in self.almacen.items():
                if (ahora - registro['timestamp']) >= registro['ttl']:
                    claves_a_borrar.append(clave)
                    
            for c in claves_a_borrar:
                del self.almacen[c]

# Instancia global para ser importada en otros módulos
cache_sistema = CacheInteligente()