import socket
import threading
import ipaddress
import sys

# ================== НАСТРОЙКИ ==================
# Адрес и порт Redis
REDIS_HOST = '127.0.0.1'
REDIS_PORT = 6380

# Порт который слушает прокси
LISTEN_PORT = 6379

# Пароль redis
REDIS_PASSWORD = 'yourpassword'

# Список доверенных IP
TRUSTED_IPS = ['127.0.0.1', '192.168.0.2']
# ===============================================

def ip_in_trusted(ip):
    for trusted in TRUSTED_IPS:
        trusted = trusted.strip()
        try:
            if '/' in trusted:
                network = ipaddress.ip_network(trusted)
                addr = ipaddress.ip_address(ip)
                if addr in network:
                    return True
            else:
                if ip == trusted:
                    return True
        except Exception as e:
            print(f"[LOG] Ошибка проверки IP для {trusted}: {e}")
    return False

def check_redis_connection():
    print(f"[INIT] Проверка подключения к Redis по адресу {REDIS_HOST}:{REDIS_PORT}...")
    try:
        test_sock = socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=5)
        test_sock.sendall(b"PING\r\n")
        response = test_sock.recv(1024)
        test_sock.close()

        if response.startswith(b"+PONG"):
            print(f"[INIT] Подключение к Redis успешно (аутентификация не требуется для PING).")
            return True
        elif response.startswith(b"-NOAUTH"):
            print(f"[INIT] Redis требует аутентификации, проверяем AUTH...")
            test_sock_auth = socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=5)
            test_sock_auth.sendall(f"AUTH {REDIS_PASSWORD}\r\n".encode())
            auth_response = test_sock_auth.recv(1024)
            test_sock_auth.close()
            if auth_response.startswith(b"+OK"):
                print(f"[INIT] Redis AUTH успешен.")
                return True
            else:
                print(f"[ERROR] [INIT] Redis AUTH не удался: {auth_response}")
                return False
        else:
            print(f"[ERROR] [INIT] Неожиданный ответ от Redis на PING: {response}")
            return False

    except socket.timeout:
        print(f"[ERROR] [INIT] Тайм-аут подключения к Redis.")
        return False
    except Exception as e:
        print(f"[ERROR] [INIT] Не удалось подключиться к Redis: {e}")
        return False

def pipe_data(source, destination, client_ip, direction):
    try:
        while True:
            data = source.recv(4096)
            if len(data) == 0:
                print(f"[LOG] [{client_ip}] {direction}: соединение закрыто другой стороной")
                break
            print(f"[LOG] [{client_ip}] Пересылка {direction}: {len(data)} байт")
            if len(data) < 100:
                print(f"[DEBUG] [{client_ip}] Данные {direction}: {data}")
            destination.sendall(data)
    except Exception as e:
        print(f"[LOG] [{client_ip}] Ошибка {direction}: {e}")
    finally:
        print(f"[LOG] [{client_ip}] Закрытие соединения {direction}")

def handle_client(client_socket, client_addr):
    client_ip = client_addr[0]
    print(f"[DEBUG] Реальный IP клиента: {client_ip}")
    print(f"[LOG] [{client_ip}] Новое подключение от {client_addr}")

    redis_sock = None
    try:
        print(f"[LOG] [{client_ip}] Подключение к Redis по адресу {REDIS_HOST}:{REDIS_PORT}")
        redis_sock = socket.create_connection((REDIS_HOST, REDIS_PORT))
        print(f"[LOG] [{client_ip}] Подключено к Redis")
    except Exception as e:
        print(f"[ERROR] [{client_ip}] Не удалось подключиться к Redis: {e}")
        client_socket.close()
        return

    is_trusted = ip_in_trusted(client_ip)
    print(f"[LOG] [{client_ip}] Статус доверенности IP: {is_trusted}")
    
    if is_trusted:
        print(f"[LOG] [{client_ip}] Доверенный IP. Автоматически добавляем AUTH к Redis.")
        try:
            auth_command = f"AUTH {REDIS_PASSWORD}\r\n"
            print(f"[LOG] [{client_ip}] Отправка команды AUTH")
            redis_sock.sendall(auth_command.encode())
            response = redis_sock.recv(1024)
            print(f"[LOG] [{client_ip}] Получен ответ AUTH: {response}")
            if not response.startswith(b"+OK"):
                print(f"[ERROR] [{client_ip}] AUTH не удался: {response}")
                client_socket.close()
                redis_sock.close()
                return
            print(f"[LOG] [{client_ip}] AUTH успешен. Клиент может использовать Redis без пароля.")
        except Exception as e:
            print(f"[ERROR] [{client_ip}] Ошибка AUTH: {e}")
            client_socket.close()
            redis_sock.close()
            return
    else:
        print(f"[LOG] [{client_ip}] Не доверенный. Пересылаем трафик как есть без изменений.")

    print(f"[LOG] [{client_ip}] Запуск двунаправленной пересылки")
    
    client_to_redis = threading.Thread(
        target=pipe_data, 
        args=(client_socket, redis_sock, client_ip, "клиент->redis")
    )
    redis_to_client = threading.Thread(
        target=pipe_data, 
        args=(redis_sock, client_socket, client_ip, "redis->клиент")
    )
    
    client_to_redis.start()
    redis_to_client.start()
    
    client_to_redis.join()
    redis_to_client.join()
    
    try:
        client_socket.close()
    except:
        pass
    try:
        redis_sock.close()
    except:
        pass
    
    print(f"[LOG] [{client_ip}] Обработчик соединения завершен")

def main():
    if not check_redis_connection():
        print("[ERROR] [INIT] Невозможно запустить прокси из-за проблемы с подключением к Redis.")
        sys.exit(1)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(('0.0.0.0', LISTEN_PORT))
    except Exception as e:
        print(f"[ERROR] [INIT] Не удалось привязаться к порту {LISTEN_PORT}: {e}")
        sys.exit(1)
    server.listen(100)
    print(f"[LOG] Прокси слушает порт :{LISTEN_PORT} -> Redis по адресу {REDIS_HOST}:{REDIS_PORT}")
    print(f"[LOG] Доверенные IP: {TRUSTED_IPS}")
    print(f"[LOG] Логика: Доверенные IP получают автоматический AUTH, недоверенные IP пересылают трафик как есть")

    try:
        while True:
            client, addr = server.accept()
            print(f"[LOG] Принято подключение от {addr}")
            client_thread = threading.Thread(target=handle_client, args=(client, addr))
            client_thread.start()
    except KeyboardInterrupt:
        print("\n[LOG] Выключение...")
    except Exception as e:
        print(f"[ERROR] [ГЛАВНЫЙ ЦИКЛ] Непредвиденная ошибка: {e}")
    finally:
        server.close()

if __name__ == "__main__":
    main()