#!/usr/bin/env python3
"""
Script de prueba simple para verificar conectividad con OpenRouter.
Este script prueba la API de forma aislada para diagnosticar problemas.
"""

import os
import requests
import json
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("MODEL_NAME")

print("=" * 60)
print("TEST DE CONECTIVIDAD OPENROUTER")
print("=" * 60)

# 1. Verificar variables de entorno
print("\n1. Verificando variables de entorno...")
print(f"   API_KEY cargada: {'SÍ' if API_KEY else 'NO'}")
if API_KEY:
    print(f"   API_KEY (primeros 10 chars): {API_KEY[:10]}...")
    print(f"   Longitud API_KEY: {len(API_KEY)}")
print(f"   MODEL cargado: {'SÍ' if MODEL else 'NO'}")
if MODEL:
    print(f"   MODEL: {MODEL}")

if not API_KEY or not MODEL:
    print("\n❌ ERROR: Faltan variables de entorno. Verifica tu archivo .env")
    exit(1)

# 2. Probar conectividad básica
print("\n2. Probando conectividad básica con OpenRouter...")
try:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Petición simple de prueba
    data = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": "Responde únicamente con: {'status': 'ok'}"}
        ]
    }
    
    print(f"   Enviando request a https://openrouter.ai/api/v1/chat/completions")
    print(f"   Modelo: {MODEL}")
    
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=data,
        timeout=30
    )
    
    print(f"\n   Status Code: {response.status_code}")
    
    if response.status_code == 200:
        print("   ✅ Conexión exitosa!")
        response_data = response.json()
        print(f"\n   Respuesta de la IA:")
        if 'choices' in response_data and response_data['choices']:
            content = response_data['choices'][0]['message']['content']
            print(f"   {content}")
        else:
            print(f"   {json.dumps(response_data, indent=2)}")
    else:
        print(f"   ❌ Error en la API")
        print(f"   Response: {response.text[:500]}")
        
        # Análisis de errores comunes
        if response.status_code == 401:
            print("\n   💡 Posible causa: API Key inválida o expirada")
        elif response.status_code == 402:
            print("\n   💡 Posible causa: Sin créditos suficientes en OpenRouter")
            print("      Algunos modelos 'free' requieren haber depositado al menos $5")
        elif response.status_code == 404:
            print(f"\n   💡 Posible causa: Modelo '{MODEL}' no encontrado")
        elif response.status_code == 429:
            print("\n   💡 Posible causa: Rate limit excedido")
        elif response.status_code >= 500:
            print("\n   💡 Error del servidor de OpenRouter")
            
except requests.exceptions.Timeout:
    print(f"\n   ❌ Timeout (30s) - No se pudo conectar a OpenRouter")
    print("   💡 Posible causa: Problemas de red o firewall bloqueando conexión")
except requests.exceptions.ConnectionError as e:
    print(f"\n   ❌ Error de conexión: {e}")
    print("   💡 Posible causa: Sin internet, DNS fallando, o IP bloqueada")
except requests.exceptions.RequestException as e:
    print(f"\n   ❌ Error en la petición: {e}")
except Exception as e:
    print(f"\n   ❌ Error inesperado: {type(e).__name__}: {e}")

print("\n" + "=" * 60)
print("TEST COMPLETADO")
print("=" * 60)
