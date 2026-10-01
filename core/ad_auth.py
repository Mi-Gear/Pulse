from ldap3 import Server, Connection, ALL
from config import AD_SERVER, AD_DOMAIN


def authenticate_ad(username, password):
    if not username or not password:
        return False

    user = f"{AD_DOMAIN}\\{username}"

    try:
        server = Server(
            AD_SERVER,
            get_info=ALL
        )

        conn = Connection(
            server,
            user=user,
            password=password,
            authentication='NTLM',
            auto_bind=True
        )

        conn.unbind()

        return True

    except Exception as e:
        print(f"AD authentication error: {e}")
        return False