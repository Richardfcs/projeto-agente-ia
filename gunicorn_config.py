# gunicorn_config.py
bind = "0.0.0.0:80"  # O Render espera que a porta seja 80 ou 10000
workers = 3          # Número de processos de trabalho
timeout = 120        # Aumenta o timeout para lidar com requisições de IA mais longas