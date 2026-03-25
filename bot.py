import os
import re
import json
import io
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import discord
from discord import app_commands
from discord.ext import commands

DATA_FILE = "data_store.json"
BACKUP_FILE = "data_store.backup.json"

DEFAULT_DATA = {
    "config": {
        "store_name": "Mi Tienda",
        "ticket_category_id": None,
        "log_channel_id": None,
        "review_channel_id": None,
        "delivery_channel_id": None,
        "staff_role_id": None,
        "admin_role_id": None,
        "customer_role_id": None,
        "notify_role_id": None,
        "payment_methods": ["PayPal", "Bizum"],
        "ticket_cooldown_seconds": 60,
        "max_orders_saved": 5000,
        "max_reviews_saved": 2000
    },
    "products": {
        "Nitro Basico": {
            "price": "4.99 EUR",
            "stock": 0,
            "description": "Entrega manual por ticket.",
            "delivery_mode": "manual",  # manual | auto
            "items": [],
            "unlimited_auto": False,
            "delivery_text": ""
        }
    },
    "orders": {},
    "reviews": [],
    "ticket_state": {
        "open_tickets": {},
        "last_ticket_ts": {}
    },
    "ticket_counter": 1
}


def deep_copy_default() -> Dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_DATA))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def compact_data(data: Dict[str, Any]) -> None:
    max_orders = safe_int(data["config"].get("max_orders_saved", 5000), 5000)
    max_reviews = safe_int(data["config"].get("max_reviews_saved", 2000), 2000)

    orders = data.get("orders", {})
    if len(orders) > max_orders:
        sorted_orders = sorted(
            orders.items(),
            key=lambda x: x[1].get("created_at", "")
        )
        trimmed = dict(sorted_orders[-max_orders:])
        data["orders"] = trimmed

    reviews = data.get("reviews", [])
    if len(reviews) > max_reviews:
        data["reviews"] = reviews[-max_reviews:]


def save_data(data: Dict[str, Any]) -> None:
    compact_data(data)
    temp_file = f"{DATA_FILE}.tmp"
    serialized = json.dumps(data, indent=2, ensure_ascii=False)

    try:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as src:
                    old_content = src.read()
                if old_content == serialized:
                    return
            except OSError:
                pass

        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(serialized)

        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as src, open(BACKUP_FILE, "w", encoding="utf-8") as dst:
                    dst.write(src.read())
            except OSError:
                pass

        os.replace(temp_file, DATA_FILE)

    except OSError as e:
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except OSError:
            pass
        raise RuntimeError(
            f"No se pudo guardar {DATA_FILE}. Posible causa: disco lleno o sin permisos. Detalle: {e}"
        ) from e


def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        data = deep_copy_default()
        save_data(data)
        return data

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        if os.path.exists(BACKUP_FILE):
            try:
                with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = deep_copy_default()
        else:
            data = deep_copy_default()
        save_data(data)
        return data

    defaults = deep_copy_default()

    for key, value in defaults.items():
        if key not in data:
            data[key] = value

    data.setdefault("config", {})
    for key, value in defaults["config"].items():
        if key not in data["config"]:
            data["config"][key] = value

    data.setdefault("orders", {})
    data.setdefault("reviews", [])
    data.setdefault("ticket_state", {"open_tickets": {}, "last_ticket_ts": {}})
    data["ticket_state"].setdefault("open_tickets", {})
    data["ticket_state"].setdefault("last_ticket_ts", {})
    data.setdefault("products", {})
    data.setdefault("ticket_counter", 1)

    for _, product_info in data.get("products", {}).items():
        product_info.setdefault("price", "N/D")
        product_info.setdefault("stock", 0)
        product_info.setdefault("description", "Sin descripción")
        product_info.setdefault("delivery_mode", "manual")
        product_info.setdefault("items", [])
        product_info.setdefault("unlimited_auto", False)
        product_info.setdefault("delivery_text", "")

    save_data(data)
    return data


data = load_data()


def get_store_name() -> str:
    return data["config"].get("store_name", "Mi Tienda")


def normalize_product_name(name: str) -> str:
    return name.strip().lower()


def find_product_key(name: str) -> Optional[str]:
    target = normalize_product_name(name)
    for key in data.get("products", {}):
        if normalize_product_name(key) == target:
            return key
    return None


def parse_quantity(value: str) -> Optional[int]:
    try:
        qty = int(value.strip())
        if qty <= 0:
            return None
        return qty
    except ValueError:
        return None


def parse_price_number(price_text: str) -> float:
    match = re.search(r"(\d+(?:[.,]\d+)?)", price_text or "")
    if not match:
        return 0.0
    return float(match.group(1).replace(",", "."))


def next_order_id() -> str:
    number = int(data.get("ticket_counter", 1))
    return f"PED-{number:05d}"


def format_stars(score: int) -> str:
    return "⭐" * max(1, min(5, score))


def is_admin_member(member: discord.Member) -> bool:
    admin_role_id = data["config"].get("admin_role_id")
    if member.guild_permissions.administrator:
        return True
    if admin_role_id:
        return any(role.id == admin_role_id for role in member.roles)
    return False


def is_staff_member(member: discord.Member) -> bool:
    if is_admin_member(member):
        return True
    staff_role_id = data["config"].get("staff_role_id")
    if staff_role_id:
        return any(role.id == staff_role_id for role in member.roles)
    return False


def get_product_stock_text(product: dict) -> str:
    if product.get("delivery_mode") == "auto" and product.get("unlimited_auto"):
        return "∞ Ilimitado"
    stock = safe_int(product.get("stock", 0))
    return str(stock) if stock > 0 else "Sin stock"


def get_product_delivery_mode(product_name: str) -> str:
    product = data["products"].get(product_name, {})
    return product.get("delivery_mode", "manual")


def has_auto_stock(product_name: str, qty: int) -> bool:
    product = data["products"].get(product_name, {})
    if product.get("delivery_mode") != "auto":
        return False

    if product.get("unlimited_auto"):
        return bool(product.get("delivery_text", "").strip())

    items = product.get("items", [])
    return len(items) >= qty


def consume_auto_items(product_name: str, qty: int) -> List[str]:
    product = data["products"].get(product_name)
    if not product:
        return []

    if product.get("unlimited_auto"):
        delivery_text = product.get("delivery_text", "").strip()
        if not delivery_text:
            return []
        return [delivery_text]

    items = product.get("items", [])
    delivered = items[:qty]
    remaining = items[qty:]
    product["items"] = remaining
    product["stock"] = len(remaining)
    save_data(data)
    return delivered


def reduce_manual_stock(product_name: str, qty: int) -> bool:
    product = data["products"].get(product_name)
    if not product:
        return False

    if product.get("delivery_mode") == "auto" and product.get("unlimited_auto"):
        return True

    current_stock = safe_int(product.get("stock", 0))
    if current_stock < qty:
        return False

    product["stock"] = current_stock - qty

    if product.get("delivery_mode") == "auto" and not product.get("unlimited_auto"):
        items = product.get("items", [])
        if len(items) > product["stock"]:
            product["items"] = items[:product["stock"]]

    save_data(data)
    return True


def get_total_sales_amount() -> float:
    total = 0.0
    for order in data["orders"].values():
        if order.get("status") in {"pagado", "completado", "entregado_auto"}:
            product_name = order.get("product", "")
            product = data["products"].get(product_name, {})
            price_number = parse_price_number(product.get("price", "0"))
            total += price_number * safe_int(order.get("quantity", 1), 1)
    return total


def get_user_orders(user_id: int) -> List[dict]:
    return [o for o in data["orders"].values() if safe_int(o.get("user_id", 0)) == int(user_id)]


def is_user_on_ticket_cooldown(user_id: int) -> tuple[bool, int]:
    cooldown = safe_int(data["config"].get("ticket_cooldown_seconds", 60), 60)
    last_ts = data["ticket_state"]["last_ticket_ts"].get(str(user_id))
    if not last_ts:
        return False, 0

    try:
        last_time = datetime.fromisoformat(last_ts)
    except ValueError:
        return False, 0

    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=timezone.utc)

    diff = utcnow() - last_time
    remaining = cooldown - int(diff.total_seconds())
    return remaining > 0, max(0, remaining)


def set_user_ticket_cooldown(user_id: int) -> None:
    data["ticket_state"]["last_ticket_ts"][str(user_id)] = now_iso()
    save_data(data)


def user_has_open_ticket(user_id: int) -> Optional[int]:
    channel_id = data["ticket_state"]["open_tickets"].get(str(user_id))
    if channel_id is None:
        return None
    return int(channel_id)


def set_user_open_ticket(user_id: int, channel_id: int) -> None:
    data["ticket_state"]["open_tickets"][str(user_id)] = channel_id
    save_data(data)


def clear_user_open_ticket(user_id: int) -> None:
    if str(user_id) in data["ticket_state"]["open_tickets"]:
        del data["ticket_state"]["open_tickets"][str(user_id)]
        save_data(data)


def create_order_record(
    order_id: str,
    user_id: int,
    username: str,
    product: str,
    quantity: int,
    payment_method: str,
    note: str,
    channel_id: int
) -> None:
    data["orders"][order_id] = {
        "order_id": order_id,
        "user_id": user_id,
        "username": username,
        "product": product,
        "quantity": quantity,
        "payment_method": payment_method or "No indicado",
        "note": note or "Sin nota",
        "channel_id": channel_id,
        "status": "abierto",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "auto_delivery": False,
        "delivered_content": [],
        "reviewed": False,
        "manual_stock_discounted": False
    }
    save_data(data)


def update_order_status(order_id: str, status: str) -> None:
    order = data["orders"].get(order_id)
    if not order:
        return
    order["status"] = status
    order["updated_at"] = now_iso()
    save_data(data)


def order_channel_match(channel_id: int) -> Optional[str]:
    for order_id, order in data.get("orders", {}).items():
        if order.get("channel_id") == channel_id:
            return order_id
    return None


def build_catalog_embed() -> discord.Embed:
    products = data.get("products", {})
    embed = discord.Embed(
        title=f"🛒 Catálogo de {get_store_name()}",
        description="Aquí tienes los productos disponibles actualmente.",
        color=discord.Color.teal(),
        timestamp=utcnow()
    )
    embed.set_footer(text="Usa el panel de compra para pedir cualquier producto")

    if not products:
        embed.add_field(name="Sin productos", value="Todavía no hay productos configurados.", inline=False)
        return embed

    for name, info in products.items():
        mode = "⚡ Automática" if info.get("delivery_mode") == "auto" else "👤 Manual"
        embed.add_field(
            name=f"📦 {name}",
            value=(
                f"**💸 Precio:** {info.get('price', 'N/D')}\n"
                f"**📊 Stock:** {get_product_stock_text(info)}\n"
                f"**🚚 Entrega:** {mode}\n"
                f"**📝 Descripción:** {info.get('description', 'Sin descripción')}"
            ),
            inline=False
        )

    return embed


def build_payment_embed() -> discord.Embed:
    methods = data["config"].get("payment_methods", [])
    embed = discord.Embed(
        title="💳 Métodos de pago",
        description="Estos son los métodos de pago disponibles ahora mismo.",
        color=discord.Color.gold(),
        timestamp=utcnow()
    )
    embed.add_field(
        name="Disponibles",
        value="\n".join(f"• {method}" for method in methods) if methods else "No configurados",
        inline=False
    )
    embed.set_footer(text="Si tienes dudas, abre un ticket desde el panel")
    return embed


def build_main_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title=f"🏪 Centro de compras — {get_store_name()}",
        description=(
            "Bienvenido a la tienda. Desde este panel puedes ver el catálogo, "
            "consultar los métodos de pago y abrir un ticket de compra."
        ),
        color=discord.Color.blurple(),
        timestamp=utcnow()
    )
    embed.add_field(
        name="📌 Cómo comprar",
        value=(
            "1. Pulsa **Abrir compra**\n"
            "2. Indica producto, cantidad y método de pago\n"
            "3. Espera a que el staff revise o a que el sistema entregue automáticamente"
        ),
        inline=False
    )
    embed.add_field(
        name="⚠️ Importante",
        value="No abras varios tickets para el mismo pedido y no cierres el ticket hasta recibir tu producto.",
        inline=False
    )
    embed.set_footer(text="Panel principal de la tienda")
    return embed


async def send_log(guild: discord.Guild, embed: discord.Embed) -> None:
    channel_id = data["config"].get("log_channel_id")
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if channel and isinstance(channel, discord.TextChannel):
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass


async def send_delivery_log(guild: discord.Guild, order_id: str) -> None:
    channel_id = data["config"].get("delivery_channel_id")
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        return

    order = data["orders"].get(order_id)
    if not order:
        return

    product = data["products"].get(order.get("product", ""), {})
    embed = discord.Embed(
        title="📦 Pedido entregado",
        color=discord.Color.green(),
        timestamp=utcnow()
    )
    embed.add_field(name="ID de pedido", value=order["order_id"], inline=False)
    embed.add_field(name="Cliente", value=f"<@{order['user_id']}>", inline=True)
    embed.add_field(name="Usuario", value=order["username"], inline=True)
    embed.add_field(name="Producto", value=order["product"], inline=True)
    embed.add_field(name="Precio", value=product.get("price", "N/D"), inline=True)
    embed.add_field(name="Cantidad", value=str(order["quantity"]), inline=True)
    embed.add_field(name="Método de pago", value=order["payment_method"], inline=True)
    embed.add_field(name="Estado", value=order["status"], inline=True)
    embed.add_field(name="Automático", value="Sí" if order.get("auto_delivery") else "No", inline=True)
    embed.add_field(name="Creado", value=order["created_at"], inline=False)
    await channel.send(embed=embed)


async def ensure_customer_role(member: discord.Member) -> None:
    role_id = data["config"].get("customer_role_id")
    if not role_id:
        return

    role = member.guild.get_role(role_id)
    if role and role not in member.roles:
        try:
            await member.add_roles(role, reason="Asignado por ticket de compra")
        except discord.Forbidden:
            pass


async def send_waiting_message(channel: discord.TextChannel, customer: discord.Member, order_id: str) -> None:
    notify_role = None
    notify_role_id = data["config"].get("notify_role_id")
    if notify_role_id:
        notify_role = channel.guild.get_role(notify_role_id)

    waiting_embed = discord.Embed(
        title="✅ Tu ticket fue creado correctamente",
        description=(
            f"Hola {customer.mention}.\n\n"
            "**Espera a que un administrador o miembro del staff te atienda.**\n"
            "No cierres el ticket y deja aquí toda la información necesaria para agilizar la compra."
        ),
        color=discord.Color.blurple(),
        timestamp=utcnow()
    )
    waiting_embed.add_field(name="ID de pedido", value=order_id, inline=False)
    waiting_embed.add_field(
        name="Qué enviar ahora",
        value="Producto, cantidad, método de pago y cualquier dato extra necesario.",
        inline=False
    )
    waiting_embed.add_field(
        name="Métodos de pago",
        value=", ".join(data["config"].get("payment_methods", [])) or "No configurados",
        inline=False
    )

    content = notify_role.mention if notify_role else None
    await channel.send(content=content, embed=waiting_embed)


async def send_review_prompt(channel: discord.TextChannel, order_id: str) -> None:
    embed = discord.Embed(
        title="⭐ ¿Quieres dejar una review?",
        description=(
            "Si tu compra fue bien, puedes dejar una reseña con el comando:\n"
            "`/dejar_review puntuacion comentario`\n\n"
            f"Pedido: **{order_id}**"
        ),
        color=discord.Color.gold(),
        timestamp=utcnow()
    )
    await channel.send(embed=embed)


async def auto_deliver_if_possible(
    ticket_channel: discord.TextChannel,
    customer: discord.Member,
    product_name: str,
    qty: int,
    order_id: str
) -> bool:
    exact_key = find_product_key(product_name)
    if not exact_key:
        return False

    if get_product_delivery_mode(exact_key) != "auto":
        return False

    if not has_auto_stock(exact_key, qty):
        await ticket_channel.send(
            embed=discord.Embed(
                title="⚠️ Sin stock automático suficiente",
                description=(
                    "No hay suficientes ítems automáticos para entregar este pedido ahora mismo. "
                    "El staff deberá revisarlo manualmente."
                ),
                color=discord.Color.orange(),
                timestamp=utcnow()
            )
        )
        return False

    delivered_items = consume_auto_items(exact_key, qty)
    if not delivered_items:
        await ticket_channel.send(
            embed=discord.Embed(
                title="⚠️ Entrega automática no configurada",
                description="Falta contenido de entrega automática para este producto.",
                color=discord.Color.orange(),
                timestamp=utcnow()
            )
        )
        return False

    delivery_embed = discord.Embed(
        title="✅ Pedido entregado correctamente",
        description=(
            f"{customer.mention}, tu pedido de **{exact_key}** fue entregado automáticamente.\n"
            "Si tienes algún problema, responde en este mismo ticket."
        ),
        color=discord.Color.green(),
        timestamp=utcnow()
    )
    delivery_embed.add_field(name="ID de pedido", value=order_id, inline=True)
    delivery_embed.add_field(name="Cantidad", value=str(qty), inline=True)
    delivery_embed.add_field(name="Estado", value="Entregado", inline=True)
    await ticket_channel.send(embed=delivery_embed)

    if data["products"][exact_key].get("unlimited_auto"):
        await ticket_channel.send(
            "**Datos de tu pedido:**\n```txt\n" + delivered_items[0] + "\n```"
        )
    else:
        await ticket_channel.send(
            "**Datos de tu pedido:**\n```txt\n" + "\n".join(delivered_items) + "\n```"
        )

    order = data["orders"].get(order_id)
    if order:
        order["status"] = "entregado_auto"
        order["updated_at"] = now_iso()
        order["auto_delivery"] = True
        order["delivered_content"] = delivered_items
        order["manual_stock_discounted"] = True
        save_data(data)

    log_embed = discord.Embed(
        title="Entrega automática completada",
        color=discord.Color.green(),
        timestamp=utcnow()
    )
    log_embed.add_field(name="Cliente", value=f"{customer} ({customer.id})", inline=False)
    log_embed.add_field(name="Producto", value=exact_key, inline=True)
    log_embed.add_field(name="Cantidad", value=str(qty), inline=True)
    log_embed.add_field(name="Pedido", value=order_id, inline=True)
    await send_log(ticket_channel.guild, log_embed)
    await send_delivery_log(ticket_channel.guild, order_id)
    await send_review_prompt(ticket_channel, order_id)
    return True


async def finalize_manual_order(
    interaction: discord.Interaction,
    order_id: str
) -> tuple[bool, str]:
    order = data["orders"].get(order_id)
    if not order:
        return False, "No encontré el pedido."

    if order.get("auto_delivery"):
        update_order_status(order_id, "completado")
        return True, "Pedido automático actualizado a completado."

    if order.get("manual_stock_discounted"):
        update_order_status(order_id, "completado")
        return True, "Pedido ya descontado anteriormente; estado actualizado."

    product_name = order.get("product", "")
    qty = safe_int(order.get("quantity", 1), 1)

    ok = reduce_manual_stock(product_name, qty)
    if not ok:
        return False, "No hay stock suficiente para completar este pedido manualmente."

    order["manual_stock_discounted"] = True
    order["status"] = "completado"
    order["updated_at"] = now_iso()
    save_data(data)

    guild = interaction.guild
    if guild:
        embed = discord.Embed(
            title="Stock descontado",
            description=f"Se descontó stock manual de **{product_name}**.",
            color=discord.Color.green(),
            timestamp=utcnow()
        )
        embed.add_field(name="Pedido", value=order_id, inline=False)
        embed.add_field(name="Cantidad", value=str(qty), inline=True)
        await send_log(guild, embed)

    return True, "Pedido completado y stock descontado."


intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        user = interaction.user
        return isinstance(user, discord.Member) and is_admin_member(user)
    return app_commands.check(predicate)


def staff_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        user = interaction.user
        return isinstance(user, discord.Member) and is_staff_member(user)
    return app_commands.check(predicate)


class MainPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛒 Abrir compra", style=discord.ButtonStyle.green, custom_id="shop:open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(OrderModal())

    @discord.ui.button(label="📦 Ver catálogo", style=discord.ButtonStyle.secondary, custom_id="shop:view_catalog")
    async def view_catalog(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=build_catalog_embed(), ephemeral=True)

    @discord.ui.button(label="💳 Métodos de pago", style=discord.ButtonStyle.secondary, custom_id="shop:view_payments")
    async def view_payments(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=build_payment_embed(), ephemeral=True)

    @discord.ui.button(label="📞 Soporte", style=discord.ButtonStyle.primary, custom_id="shop:support_info")
    async def support_info(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(
            title="📞 Soporte",
            description="Si necesitas ayuda, abre un ticket con el botón de compra y explica tu problema con el mayor detalle posible.",
            color=discord.Color.blurple(),
            timestamp=utcnow()
        )
        embed.add_field(
            name="Consejo",
            value="Incluye capturas, número de pedido y método de pago si ya compraste.",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💰 Marcar pagado", style=discord.ButtonStyle.primary, custom_id="shop:paid")
    async def mark_paid(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_staff_member(member):
            await interaction.response.send_message("Solo el staff puede usar este botón.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Este botón solo puede usarse en tickets.", ephemeral=True)
            return

        order_id = order_channel_match(channel.id)
        if order_id:
            update_order_status(order_id, "pagado")

        embed = discord.Embed(
            title="💰 Pedido marcado como pagado",
            description=f"Validado por {member.mention}",
            color=discord.Color.gold(),
            timestamp=utcnow()
        )
        if order_id:
            embed.add_field(name="Pedido", value=order_id, inline=False)

        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="✅ Marcar completado", style=discord.ButtonStyle.success, custom_id="shop:completed")
    async def mark_completed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_staff_member(member):
            await interaction.response.send_message("Solo el staff puede usar este botón.", ephemeral=True)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Este botón debe usarse dentro de un ticket.", ephemeral=True)
            return

        order_id = order_channel_match(channel.id)
        if not order_id:
            await interaction.response.send_message("No encontré ningún pedido asociado a este canal.", ephemeral=True)
            return

        ok, message = await finalize_manual_order(interaction, order_id)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ Pedido completado",
            description=f"Completado por {member.mention}",
            color=discord.Color.green(),
            timestamp=utcnow()
        )
        embed.add_field(name="Pedido", value=order_id, inline=False)
        await interaction.response.send_message(embed=embed)

        await send_delivery_log(channel.guild, order_id)
        await send_review_prompt(channel, order_id)

    @discord.ui.button(label="🔒 Cerrar ticket", style=discord.ButtonStyle.danger, custom_id="shop:close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_staff_member(member):
            await interaction.response.send_message("Solo el staff puede cerrar tickets.", ephemeral=True)
            return

        channel = interaction.channel
        guild = interaction.guild

        if not isinstance(channel, discord.TextChannel) or guild is None:
            await interaction.response.send_message("No pude cerrar este ticket.", ephemeral=True)
            return

        order_id = order_channel_match(channel.id)
        order = data["orders"].get(order_id) if order_id else None

        if order:
            clear_user_open_ticket(safe_int(order["user_id"]))

        await interaction.response.send_message("Cerrando ticket en 5 segundos...")
        await asyncio.sleep(5)

        transcript_lines = []
        try:
            async for message in channel.history(limit=300, oldest_first=True):
                clean = message.content.replace("\n", " ") if message.content else "[embed/archivo]"
                transcript_lines.append(
                    f"[{message.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {message.author}: {clean}"
                )
        except discord.Forbidden:
            transcript_lines.append("No se pudo leer el historial del ticket.")

        transcript_text = "\n".join(transcript_lines)
        transcript_file = discord.File(
            io.BytesIO(transcript_text.encode("utf-8")),
            filename=f"transcript_{channel.id}.txt"
        )

        log_channel_id = data["config"].get("log_channel_id")
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
        if isinstance(log_channel, discord.TextChannel):
            try:
                embed = discord.Embed(
                    title="Ticket cerrado",
                    color=discord.Color.red(),
                    timestamp=utcnow()
                )
                embed.add_field(name="Canal", value=channel.name, inline=False)
                if order_id:
                    embed.add_field(name="Pedido", value=order_id, inline=False)
                embed.add_field(name="Cerrado por", value=f"{member} ({member.id})", inline=False)
                await log_channel.send(embed=embed, file=transcript_file)
            except discord.Forbidden:
                pass

        try:
            await channel.delete(reason=f"Ticket cerrado por {member}")
        except discord.Forbidden:
            pass


class OrderModal(discord.ui.Modal, title="Nuevo pedido"):
    producto = discord.ui.TextInput(label="Producto", placeholder="Ej: Nitro Basico", max_length=100)
    cantidad = discord.ui.TextInput(label="Cantidad", placeholder="Ej: 1", max_length=5)
    metodo_pago = discord.ui.TextInput(
        label="Método de pago",
        placeholder="PayPal, Bizum, Transferencia...",
        max_length=50,
        required=False
    )
    nota = discord.ui.TextInput(
        label="Nota adicional",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user

        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("No pude procesar el pedido aquí.", ephemeral=True)
            return

        existing_channel_id = user_has_open_ticket(member.id)
        if existing_channel_id:
            existing_channel = guild.get_channel(existing_channel_id)
            if existing_channel:
                await interaction.response.send_message(
                    f"Ya tienes un ticket abierto en {existing_channel.mention}.",
                    ephemeral=True
                )
                return
            clear_user_open_ticket(member.id)

        on_cooldown, remaining = is_user_on_ticket_cooldown(member.id)
        if on_cooldown:
            await interaction.response.send_message(
                f"Espera {remaining} segundos antes de abrir otro ticket.",
                ephemeral=True
            )
            return

        qty = parse_quantity(self.cantidad.value)
        if qty is None:
            await interaction.response.send_message("La cantidad debe ser un número mayor que 0.", ephemeral=True)
            return

        matched_product = find_product_key(self.producto.value)
        if not matched_product:
            await interaction.response.send_message("Ese producto no existe en el catálogo.", ephemeral=True)
            return

        product = data["products"][matched_product]

        if product.get("delivery_mode") == "auto":
            if product.get("unlimited_auto"):
                if not product.get("delivery_text", "").strip():
                    await interaction.response.send_message(
                        "Este producto tiene entrega automática ilimitada, pero no tiene contenido configurado.",
                        ephemeral=True
                    )
                    return
            else:
                if safe_int(product.get("stock", 0)) < qty:
                    await interaction.response.send_message("No hay stock automático suficiente para ese producto.", ephemeral=True)
                    return
        else:
            if safe_int(product.get("stock", 0)) < qty:
                await interaction.response.send_message("No hay stock suficiente para ese producto.", ephemeral=True)
                return

        category_id = data["config"].get("ticket_category_id")
        category = guild.get_channel(category_id) if category_id else None

        order_id = next_order_id()
        data["ticket_counter"] += 1
        save_data(data)

        bot_member = guild.me or guild.get_member(bot.user.id if bot.user else 0)
        if bot_member is None:
            await interaction.response.send_message("No pude obtener mis permisos dentro del servidor.", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )
        }

        staff_role_id = data["config"].get("staff_role_id")
        if staff_role_id:
            staff_role = guild.get_role(staff_role_id)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )

        admin_role_id = data["config"].get("admin_role_id")
        if admin_role_id:
            admin_role = guild.get_role(admin_role_id)
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True
                )

        channel_name = f"pedido-{order_id.lower()}"

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name[:100],
                overwrites=overwrites,
                category=category if isinstance(category, discord.CategoryChannel) else None,
                topic=f"Pedido:{order_id} | Cliente:{member.id} | Producto:{matched_product} | Cantidad:{qty}"
            )
        except discord.Forbidden:
            await interaction.response.send_message("No tengo permisos para crear tickets.", ephemeral=True)
            return

        set_user_open_ticket(member.id, ticket_channel.id)
        set_user_ticket_cooldown(member.id)
        await ensure_customer_role(member)

        create_order_record(
            order_id=order_id,
            user_id=member.id,
            username=str(member),
            product=matched_product,
            quantity=qty,
            payment_method=self.metodo_pago.value,
            note=self.nota.value,
            channel_id=ticket_channel.id
        )

        await send_waiting_message(ticket_channel, member, order_id)

        embed = discord.Embed(
            title="🧾 Nuevo ticket de compra",
            description="Tu pedido fue registrado correctamente.",
            color=discord.Color.green(),
            timestamp=utcnow()
        )
        embed.add_field(name="ID de pedido", value=order_id, inline=False)
        embed.add_field(name="Cliente", value=member.mention, inline=False)
        embed.add_field(name="Producto", value=matched_product, inline=True)
        embed.add_field(name="Cantidad", value=str(qty), inline=True)
        embed.add_field(name="Método de pago", value=self.metodo_pago.value or "No indicado", inline=False)
        embed.add_field(name="Nota", value=self.nota.value or "Sin nota", inline=False)
        embed.set_footer(text="Usa los botones de abajo para gestionar el pedido")
        await ticket_channel.send(embed=embed, view=TicketActionsView())

        auto_delivered = await auto_deliver_if_possible(ticket_channel, member, matched_product, qty, order_id)
        if not auto_delivered:
            await ticket_channel.send(
                embed=discord.Embed(
                    title="⏳ Pedido pendiente de revisión",
                    description="El staff revisará tu pago y completará la entrega lo antes posible.",
                    color=discord.Color.gold(),
                    timestamp=utcnow()
                )
            )

        log_embed = discord.Embed(
            title="Ticket creado",
            color=discord.Color.blurple(),
            timestamp=utcnow()
        )
        log_embed.add_field(name="Pedido", value=order_id, inline=False)
        log_embed.add_field(name="Canal", value=ticket_channel.mention, inline=False)
        log_embed.add_field(name="Cliente", value=f"{member} ({member.id})", inline=False)
        log_embed.add_field(name="Producto", value=matched_product, inline=True)
        log_embed.add_field(name="Cantidad", value=str(qty), inline=True)
        log_embed.add_field(name="Entrega", value="Automática" if auto_delivered else "Manual", inline=True)
        await send_log(guild, log_embed)

        await interaction.response.send_message(
            f"Tu ticket se creó en {ticket_channel.mention}",
            ephemeral=True
        )


@bot.event
async def on_ready() -> None:
    bot.add_view(MainPanelView())
    bot.add_view(TicketActionsView())
    try:
        synced = await bot.tree.sync()
        print(f"Bot listo como {bot.user} | comandos sincronizados: {len(synced)}")
    except Exception as e:
        print(f"Error al sincronizar comandos: {e}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.CheckFailure):
        msg = "No tienes permisos para usar este comando."
    elif isinstance(error, app_commands.CommandInvokeError):
        msg = f"Error interno: {error.original}"
    else:
        msg = "Ocurrió un error al ejecutar el comando."

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="configurar_tienda", description="Configura categoría, logs, reviews, entregas y roles")
@admin_only()
async def configurar_tienda(
    interaction: discord.Interaction,
    categoria_tickets: discord.CategoryChannel,
    canal_logs: discord.TextChannel,
    canal_reviews: discord.TextChannel,
    canal_entregas: discord.TextChannel,
    rol_staff: discord.Role,
    rol_admin: discord.Role,
    rol_cliente: Optional[discord.Role] = None,
    rol_aviso: Optional[discord.Role] = None
) -> None:
    data["config"]["ticket_category_id"] = categoria_tickets.id
    data["config"]["log_channel_id"] = canal_logs.id
    data["config"]["review_channel_id"] = canal_reviews.id
    data["config"]["delivery_channel_id"] = canal_entregas.id
    data["config"]["staff_role_id"] = rol_staff.id
    data["config"]["admin_role_id"] = rol_admin.id
    data["config"]["customer_role_id"] = rol_cliente.id if rol_cliente else None
    data["config"]["notify_role_id"] = rol_aviso.id if rol_aviso else None
    save_data(data)

    embed = discord.Embed(
        title="✅ Tienda configurada",
        color=discord.Color.green(),
        timestamp=utcnow()
    )
    embed.add_field(name="Categoría tickets", value=categoria_tickets.mention, inline=False)
    embed.add_field(name="Canal logs", value=canal_logs.mention, inline=False)
    embed.add_field(name="Canal reviews", value=canal_reviews.mention, inline=False)
    embed.add_field(name="Canal entregas", value=canal_entregas.mention, inline=False)
    embed.add_field(name="Rol staff", value=rol_staff.mention, inline=True)
    embed.add_field(name="Rol admin", value=rol_admin.mention, inline=True)
    embed.add_field(name="Rol cliente", value=rol_cliente.mention if rol_cliente else "No configurado", inline=True)
    embed.add_field(name="Rol aviso", value=rol_aviso.mention if rol_aviso else "No configurado", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="nombre_tienda", description="Cambia el nombre mostrado de la tienda")
@admin_only()
async def nombre_tienda(interaction: discord.Interaction, nombre: str) -> None:
    data["config"]["store_name"] = nombre
    save_data(data)
    await interaction.response.send_message(f"Nombre de la tienda actualizado a **{nombre}**.", ephemeral=True)


@bot.tree.command(name="cooldown_tickets", description="Configura el cooldown de tickets en segundos")
@admin_only()
async def cooldown_tickets(interaction: discord.Interaction, segundos: app_commands.Range[int, 0, 3600]) -> None:
    data["config"]["ticket_cooldown_seconds"] = segundos
    save_data(data)
    await interaction.response.send_message(
        f"Cooldown de tickets actualizado a **{segundos}** segundos.",
        ephemeral=True
    )


@bot.tree.command(name="panel_compras", description="Publica el panel principal de la tienda")
@admin_only()
async def panel_compras(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Este comando debe usarse en un canal de texto.", ephemeral=True)
        return
    await interaction.channel.send(embed=build_main_panel_embed(), view=MainPanelView())
    await interaction.response.send_message("Panel publicado correctamente.", ephemeral=True)


@bot.tree.command(name="catalogo", description="Muestra el catálogo de la tienda")
async def catalogo(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(embed=build_catalog_embed(), ephemeral=True)


@bot.tree.command(name="agregar_producto", description="Agrega o actualiza un producto")
@admin_only()
async def agregar_producto(
    interaction: discord.Interaction,
    nombre: str,
    precio: str,
    stock: app_commands.Range[int, 0, 100000],
    descripcion: str,
    entrega_automatica: bool = False,
    stock_ilimitado: bool = False
) -> None:
    current_items = data["products"].get(nombre, {}).get("items", [])
    current_delivery_text = data["products"].get(nombre, {}).get("delivery_text", "")

    data["products"][nombre] = {
        "price": precio,
        "stock": stock,
        "description": descripcion,
        "delivery_mode": "auto" if entrega_automatica else "manual",
        "items": current_items,
        "unlimited_auto": stock_ilimitado if entrega_automatica else False,
        "delivery_text": current_delivery_text
    }

    if entrega_automatica and stock_ilimitado:
        data["products"][nombre]["stock"] = 999999999
    elif entrega_automatica:
        data["products"][nombre]["stock"] = len(current_items)

    save_data(data)
    await interaction.response.send_message(f"Producto **{nombre}** guardado correctamente.", ephemeral=True)


@bot.tree.command(name="configurar_entrega_ilimitada", description="Configura entrega automática ilimitada para un producto")
@admin_only()
async def configurar_entrega_ilimitada(interaction: discord.Interaction, producto: str, contenido: str) -> None:
    product_key = find_product_key(producto)
    if not product_key:
        await interaction.response.send_message("Ese producto no existe.", ephemeral=True)
        return

    data["products"][product_key]["delivery_mode"] = "auto"
    data["products"][product_key]["unlimited_auto"] = True
    data["products"][product_key]["delivery_text"] = contenido.strip()
    data["products"][product_key]["stock"] = 999999999
    save_data(data)

    await interaction.response.send_message(
        f"Entrega automática ilimitada configurada para **{product_key}**.",
        ephemeral=True
    )


@bot.tree.command(name="eliminar_producto", description="Elimina un producto")
@admin_only()
async def eliminar_producto(interaction: discord.Interaction, nombre: str) -> None:
    product_key = find_product_key(nombre)
    if not product_key:
        await interaction.response.send_message("Ese producto no existe.", ephemeral=True)
        return

    del data["products"][product_key]
    save_data(data)
    await interaction.response.send_message(f"Producto **{product_key}** eliminado.", ephemeral=True)


@bot.tree.command(name="stock", description="Ajusta el stock de un producto")
@staff_only()
async def stock(interaction: discord.Interaction, nombre: str, nuevo_stock: app_commands.Range[int, 0, 100000]) -> None:
    product_key = find_product_key(nombre)
    if not product_key:
        await interaction.response.send_message("Ese producto no existe.", ephemeral=True)
        return

    if data["products"][product_key].get("unlimited_auto"):
        await interaction.response.send_message(
            "Ese producto tiene stock automático ilimitado. No necesita ajuste manual.",
            ephemeral=True
        )
        return

    data["products"][product_key]["stock"] = nuevo_stock

    if data["products"][product_key].get("delivery_mode") == "auto":
        items = data["products"][product_key].get("items", [])
        if len(items) > nuevo_stock:
            data["products"][product_key]["items"] = items[:nuevo_stock]

    save_data(data)
    await interaction.response.send_message(
        f"Stock de **{product_key}** actualizado a **{nuevo_stock}**.",
        ephemeral=True
    )


@bot.tree.command(name="pedido_pagado", description="Marca un pedido como pagado en el ticket actual")
@staff_only()
async def pedido_pagado(interaction: discord.Interaction) -> None:
    if interaction.channel is None:
        await interaction.response.send_message("Este comando debe usarse dentro de un ticket.", ephemeral=True)
        return

    order_id = order_channel_match(interaction.channel.id)
    if order_id:
        update_order_status(order_id, "pagado")

    embed = discord.Embed(
        title="💰 Pedido marcado como pagado",
        description=f"Validado por {interaction.user.mention}",
        color=discord.Color.gold(),
        timestamp=utcnow()
    )
    if order_id:
        embed.add_field(name="Pedido", value=order_id, inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="pedido_completado", description="Marca un pedido como completado en el ticket actual")
@staff_only()
async def pedido_completado(interaction: discord.Interaction) -> None:
    if interaction.channel is None:
        await interaction.response.send_message("Este comando debe usarse dentro de un ticket.", ephemeral=True)
        return

    order_id = order_channel_match(interaction.channel.id)
    if not order_id:
        await interaction.response.send_message("No encontré ningún pedido en este canal.", ephemeral=True)
        return

    ok, message = await finalize_manual_order(interaction, order_id)
    if not ok:
        await interaction.response.send_message(message, ephemeral=True)
        return

    embed = discord.Embed(
        title="✅ Pedido completado",
        description=f"Gestionado por {interaction.user.mention}",
        color=discord.Color.green(),
        timestamp=utcnow()
    )
    embed.add_field(name="Pedido", value=order_id, inline=False)

    await interaction.response.send_message(embed=embed)

    if interaction.guild and isinstance(interaction.channel, discord.TextChannel):
        await send_delivery_log(interaction.guild, order_id)
        await send_review_prompt(interaction.channel, order_id)


@bot.tree.command(name="agregar_stock_automatico", description="Añade líneas de stock para entrega automática")
@admin_only()
async def agregar_stock_automatico(interaction: discord.Interaction, producto: str, contenido: str) -> None:
    product_key = find_product_key(producto)
    if not product_key:
        await interaction.response.send_message("Ese producto no existe.", ephemeral=True)
        return

    lineas = [line.strip() for line in contenido.splitlines() if line.strip()]
    if not lineas:
        await interaction.response.send_message("Debes pegar al menos una línea de stock automático.", ephemeral=True)
        return

    data["products"][product_key].setdefault("items", []).extend(lineas)
    data["products"][product_key]["delivery_mode"] = "auto"
    data["products"][product_key]["unlimited_auto"] = False
    data["products"][product_key]["stock"] = len(data["products"][product_key]["items"])
    save_data(data)

    await interaction.response.send_message(
        f"Añadidos **{len(lineas)}** ítems automáticos a **{product_key}**.",
        ephemeral=True
    )


@bot.tree.command(name="publicar_catalogo", description="Publica el catálogo visible para todos en el canal actual")
@admin_only()
async def publicar_catalogo(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Este comando debe usarse en un canal de texto.", ephemeral=True)
        return
    await interaction.channel.send(embed=build_catalog_embed())
    await interaction.response.send_message("Catálogo publicado en este canal.", ephemeral=True)


@bot.tree.command(name="metodos_pago", description="Configura los métodos de pago visibles en los tickets")
@admin_only()
async def metodos_pago(interaction: discord.Interaction, metodos: str) -> None:
    valores = [m.strip() for m in metodos.split(",") if m.strip()]
    if not valores:
        await interaction.response.send_message("Debes indicar al menos un método de pago.", ephemeral=True)
        return

    data["config"]["payment_methods"] = valores
    save_data(data)
    await interaction.response.send_message("Métodos de pago actualizados correctamente.", ephemeral=True)


@bot.tree.command(name="publicar_metodos_pago", description="Publica los métodos de pago visibles para todos")
@admin_only()
async def publicar_metodos_pago(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Este comando debe usarse en un canal de texto.", ephemeral=True)
        return
    await interaction.channel.send(embed=build_payment_embed())
    await interaction.response.send_message("Métodos de pago publicados en este canal.", ephemeral=True)


@bot.tree.command(name="dejar_review", description="Deja una review de tu compra")
async def dejar_review(
    interaction: discord.Interaction,
    puntuacion: app_commands.Range[int, 1, 5],
    comentario: str
) -> None:
    review_channel_id = data["config"].get("review_channel_id")
    if not review_channel_id:
        await interaction.response.send_message("No hay canal de reviews configurado.", ephemeral=True)
        return

    order_id = None
    if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
        order_id = order_channel_match(interaction.channel.id)

    review_record = {
        "user_id": interaction.user.id,
        "username": str(interaction.user),
        "score": puntuacion,
        "comment": comentario,
        "order_id": order_id,
        "created_at": now_iso()
    }
    data["reviews"].append(review_record)

    if order_id and order_id in data["orders"]:
        data["orders"][order_id]["reviewed"] = True

    save_data(data)

    review_channel = interaction.guild.get_channel(review_channel_id) if interaction.guild else None
    if isinstance(review_channel, discord.TextChannel):
        embed = discord.Embed(
            title="⭐ Nueva review",
            description=comentario,
            color=discord.Color.gold(),
            timestamp=utcnow()
        )
        embed.add_field(name="Cliente", value=interaction.user.mention, inline=True)
        embed.add_field(name="Puntuación", value=format_stars(puntuacion), inline=True)
        embed.add_field(name="Pedido", value=order_id or "No especificado", inline=True)
        await review_channel.send(embed=embed)

    await interaction.response.send_message("Gracias por tu review. Ya fue enviada.", ephemeral=True)


@bot.tree.command(name="ventas_totales", description="Muestra el total vendido")
@admin_only()
async def ventas_totales(interaction: discord.Interaction) -> None:
    total = get_total_sales_amount()
    embed = discord.Embed(
        title="📈 Ventas totales",
        description=f"Total vendido: **{total:.2f}**",
        color=discord.Color.green(),
        timestamp=utcnow()
    )
    embed.add_field(name="Pedidos guardados", value=str(len(data['orders'])), inline=True)
    embed.add_field(name="Reviews", value=str(len(data['reviews'])), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="historial_cliente", description="Muestra el historial de compras de un cliente")
@staff_only()
async def historial_cliente(interaction: discord.Interaction, cliente: discord.Member) -> None:
    orders = get_user_orders(cliente.id)
    if not orders:
        await interaction.response.send_message("Ese cliente no tiene pedidos guardados.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📚 Historial de {cliente}",
        color=discord.Color.blurple(),
        timestamp=utcnow()
    )

    for order in orders[:10]:
        embed.add_field(
            name=order["order_id"],
            value=(
                f"**Producto:** {order['product']}\n"
                f"**Cantidad:** {order['quantity']}\n"
                f"**Estado:** {order['status']}\n"
                f"**Pago:** {order['payment_method']}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="pedido_info", description="Muestra la información de un pedido")
@staff_only()
async def pedido_info(interaction: discord.Interaction, pedido_id: str) -> None:
    order = data["orders"].get(pedido_id.upper())
    if not order:
        await interaction.response.send_message("No existe ese pedido.", ephemeral=True)
        return

    product = data["products"].get(order["product"], {})
    embed = discord.Embed(
        title=f"🧾 Información del pedido {pedido_id.upper()}",
        color=discord.Color.blurple(),
        timestamp=utcnow()
    )
    embed.add_field(name="Cliente", value=f"<@{order['user_id']}>", inline=True)
    embed.add_field(name="Producto", value=order["product"], inline=True)
    embed.add_field(name="Precio", value=product.get("price", "N/D"), inline=True)
    embed.add_field(name="Cantidad", value=str(order["quantity"]), inline=True)
    embed.add_field(name="Estado", value=order["status"], inline=True)
    embed.add_field(name="Pago", value=order["payment_method"], inline=True)
    embed.add_field(name="Nota", value=order["note"], inline=False)
    embed.add_field(name="Creado", value=order["created_at"], inline=False)
    embed.add_field(name="Stock descontado", value="Sí" if order.get("manual_stock_discounted") else "No", inline=True)
    embed.add_field(name="Auto entrega", value="Sí" if order.get("auto_delivery") else "No", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="borrar_historial_ventas", description="Borra todo el historial de ventas guardado")
@admin_only()
async def borrar_historial_ventas(interaction: discord.Interaction) -> None:
    total_orders = len(data["orders"])
    total_reviews = len(data["reviews"])
    data["orders"] = {}
    data["reviews"] = []
    save_data(data)

    embed = discord.Embed(
        title="🗑️ Historial eliminado",
        description="Se borró el historial de ventas y reviews guardadas.",
        color=discord.Color.red(),
        timestamp=utcnow()
    )
    embed.add_field(name="Pedidos borrados", value=str(total_orders), inline=True)
    embed.add_field(name="Reviews borradas", value=str(total_reviews), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="estado_tienda", description="Muestra estado general del sistema")
@admin_only()
async def estado_tienda(interaction: discord.Interaction) -> None:
    total_products = len(data.get("products", {}))
    total_orders = len(data.get("orders", {}))
    total_reviews = len(data.get("reviews", []))
    open_tickets = len(data["ticket_state"].get("open_tickets", {}))

    embed = discord.Embed(
        title="📊 Estado de la tienda",
        color=discord.Color.blurple(),
        timestamp=utcnow()
    )
    embed.add_field(name="Productos", value=str(total_products), inline=True)
    embed.add_field(name="Pedidos guardados", value=str(total_orders), inline=True)
    embed.add_field(name="Reviews", value=str(total_reviews), inline=True)
    embed.add_field(name="Tickets abiertos registrados", value=str(open_tickets), inline=True)
    embed.add_field(name="Métodos de pago", value=", ".join(data["config"].get("payment_methods", [])) or "Ninguno", inline=False)
    embed.add_field(name="Nombre tienda", value=get_store_name(), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="backup_datos", description="Envía una copia del archivo de datos")
@admin_only()
async def backup_datos(interaction: discord.Interaction) -> None:
    try:
        save_data(data)
        if not os.path.exists(DATA_FILE):
            await interaction.response.send_message("No existe el archivo de datos.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Aquí tienes el backup actual:",
            ephemeral=True,
            file=discord.File(DATA_FILE)
        )
    except Exception as e:
        await interaction.response.send_message(f"No se pudo generar el backup: {e}", ephemeral=True)


@bot.tree.command(name="ayuda_tienda", description="Muestra los comandos del bot")
async def ayuda_tienda(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="📘 Ayuda del bot de tienda",
        color=discord.Color.blurple(),
        timestamp=utcnow()
    )
    embed.add_field(
        name="👤 Clientes",
        value="`/catalogo`, `/dejar_review`",
        inline=False
    )
    embed.add_field(
        name="👑 Admins",
        value=(
            "`/configurar_tienda`, `/nombre_tienda`, `/cooldown_tickets`, `/panel_compras`, "
            "`/agregar_producto`, `/configurar_entrega_ilimitada`, `/eliminar_producto`, "
            "`/agregar_stock_automatico`, `/publicar_catalogo`, `/publicar_metodos_pago`, "
            "`/metodos_pago`, `/ventas_totales`, `/borrar_historial_ventas`, `/estado_tienda`, `/backup_datos`"
        ),
        inline=False
    )
    embed.add_field(
        name="🛠️ Staff",
        value="`/stock`, `/pedido_pagado`, `/pedido_completado`, `/historial_cliente`, `/pedido_info`",
        inline=False
    )
    embed.set_footer(text="Usa el panel con botones para una experiencia más cómoda")
    await interaction.response.send_message(embed=embed, ephemeral=True)
import discord

intents = discord.Intents.default()
bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    print(f'Conectado como {bot.user}')

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content == "!hola":
        await message.channel.send("Hola 👋")

bot.run("MTQ4NTczNjM1NjM4NTY1Mjg0MQ.GIO51s.fsBF7dY0QQ_nOkfNWMyBWMQdZgzAY00WXbw_VY")
   