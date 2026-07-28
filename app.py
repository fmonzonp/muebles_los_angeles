from __future__ import annotations

import csv
import hmac
import io
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Generator

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    case,
    create_engine,
    func,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
password_hasher = PasswordHasher()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_database_url(value: str) -> str:
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value[len("postgresql://") :]
    return value


DATABASE_URL = normalize_database_url(
    os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'muebles_nube.db'}")
)
ENGINE_OPTIONS: dict[str, Any] = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    ENGINE_OPTIONS["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **ENGINE_OPTIONS)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="VENDEDOR")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"

    def set_password(self, password: str) -> None:
        self.password_hash = password_hasher.hash(password)

    def check_password(self, password: str) -> bool:
        try:
            return password_hasher.verify(self.password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False


class AnonymousUser:
    id = None
    username = ""
    full_name = ""
    role = ""
    active = False
    is_authenticated = False
    is_admin = False


ANONYMOUS = AnonymousUser()


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    cedula: Mapped[str | None] = mapped_column(String(60), index=True)
    phone: Mapped[str | None] = mapped_column(String(80), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)

    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    layaways: Mapped[list["Layaway"]] = relationship(back_populates="client")


class Layaway(Base):
    __tablename__ = "layaways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str | None] = mapped_column(String(30), unique=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    product: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    installments: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    initial_payment: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVO", index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)

    client: Mapped[Client] = relationship(back_populates="layaways")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    updated_by: Mapped[User | None] = relationship(foreign_keys=[updated_by_id])
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="layaway",
        cascade="all, delete-orphan",
        order_by=lambda: (Payment.payment_date.desc(), Payment.id.desc()),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_number: Mapped[str | None] = mapped_column(String(30), unique=True, index=True)
    layaway_id: Mapped[int] = mapped_column(ForeignKey("layaways.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    previous_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    new_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)

    layaway: Mapped[Layaway] = relationship(back_populates="payments")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, index=True)

    user: Mapped[User | None] = relationship()


Base.metadata.create_all(engine)


def bootstrap_admin() -> None:
    with SessionLocal() as db:
        if (db.scalar(select(func.count(User.id))) or 0) > 0:
            return
        production = os.environ.get("APP_ENV", "development").lower() == "production"
        password = os.environ.get("ADMIN_PASSWORD")
        if not password and not production:
            password = "Admin123!"
        if not password:
            return
        user = User(
            username=os.environ.get("ADMIN_USERNAME", "admin").strip().lower(),
            full_name=os.environ.get("ADMIN_NAME", "Administrador").strip(),
            role="ADMIN",
            active=True,
            password_hash="",
        )
        user.set_password(password)
        db.add(user)
        db.commit()


bootstrap_admin()

production = os.environ.get("APP_ENV", "development").lower() == "production"
app = FastAPI(title="Control de Clientes y Apartados")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "clave-local-cambiar-en-produccion"),
    session_cookie="mla_session",
    max_age=60 * 60 * 12,
    same_site="lax",
    https_only=production,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def crc_filter(value: Any) -> str:
    amount = Decimal(value or 0)
    return f"₡{amount:,.2f}"


def date_cr_filter(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(value)


def datetime_cr_filter(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value)


templates.env.filters.update(crc=crc_filter, date_cr=date_cr_filter, datetime_cr=datetime_cr_filter)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request, db: Session) -> User | AnonymousUser:
    cached = getattr(request.state, "current_user", None)
    if cached is not None:
        return cached
    user_id = request.session.get("user_id")
    user = db.get(User, int(user_id)) if user_id else None
    if not user or not user.active:
        request.session.pop("user_id", None)
        request.state.current_user = ANONYMOUS
    else:
        request.state.current_user = user
    return request.state.current_user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if not user.is_authenticated:
        target = str(request.url.path)
        raise HTTPException(status_code=303, headers={"Location": f"/login?next={target}"})
    return user  # type: ignore[return-value]


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="No tiene permiso para realizar esta acción.")
    return user


def csrf_token(request: Request) -> str:
    token = request.session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["_csrf_token"] = token
    return token


async def secure_form(request: Request):
    form = await request.form()
    expected = request.session.get("_csrf_token", "")
    supplied = str(form.get("_csrf_token", ""))
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=400, detail="Token de seguridad inválido. Recargue la página.")
    return form


def add_flash(request: Request, message: str, category: str = "success") -> None:
    flashes = request.session.setdefault("_flashes", [])
    flashes.append([category, message])
    request.session["_flashes"] = flashes[-8:]


def pop_flashes(request: Request) -> list[list[str]]:
    return request.session.pop("_flashes", [])


def endpoint_name(request: Request) -> str:
    endpoint = request.scope.get("endpoint")
    return getattr(endpoint, "__name__", "")


def make_url_for(request: Request):
    route_path_keys = {
        "static": {"path"},
        "client_edit": {"client_id"},
        "client_edit_submit": {"client_id"},
        "layaway_detail": {"layaway_id"},
        "layaway_edit": {"layaway_id"},
        "layaway_edit_submit": {"layaway_id"},
        "payment_add": {"layaway_id"},
        "receipt": {"payment_id"},
        "user_edit": {"user_id"},
        "user_edit_submit": {"user_id"},
    }

    def url_for(name: str, **params: Any) -> str:
        if name == "static" and "filename" in params:
            params["path"] = params.pop("filename")
        keys = route_path_keys.get(name, set())
        path_params = {key: value for key, value in params.items() if key in keys}
        query_params = {key: value for key, value in params.items() if key not in keys}
        url = request.url_for(name, **path_params)
        if query_params:
            url = url.include_query_params(**query_params)
        return str(url)

    return url_for


def render(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200):
    context = dict(context or {})
    user = getattr(request.state, "current_user", ANONYMOUS)
    context.update(
        {
            "request": request,
            "current_user": user,
            "csrf_token": lambda: csrf_token(request),
            "today_iso": date.today().isoformat(),
            "endpoint_name": endpoint_name(request),
            "url_for": make_url_for(request),
            "flashes": pop_flashes(request),
        }
    )
    return templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)


def redirect(name: str, request: Request, **params: Any) -> RedirectResponse:
    return RedirectResponse(make_url_for(request)(name, **params), status_code=303)


def parse_money(raw: Any, field_name: str) -> Decimal:
    try:
        cleaned = str(raw or "0").replace("₡", "").replace(",", "").strip()
        value = Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_name} debe ser un monto válido.")
    if value < 0:
        raise ValueError(f"{field_name} no puede ser negativo.")
    return value


def parse_date(raw: Any, field_name: str) -> date:
    try:
        return datetime.strptime(str(raw or ""), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field_name} no es una fecha válida.")


def computed_status(balance: Decimal, due_date: date) -> str:
    if Decimal(balance or 0) <= Decimal("0.009"):
        return "PAGADO"
    return "VENCIDO" if due_date < date.today() else "ACTIVO"


def refresh_statuses(db: Session, layaway_id: int | None = None) -> None:
    statement = select(Layaway)
    if layaway_id is not None:
        statement = statement.where(Layaway.id == layaway_id)
    changed = False
    for item in db.scalars(statement):
        status = computed_status(Decimal(item.balance), item.due_date)
        if item.status != status:
            item.status = status
            changed = True
    if changed:
        db.commit()


def audit(db: Session, user: User | AnonymousUser, action: str, entity_type: str, entity_id: int | None, detail: str = "") -> None:
    db.add(
        AuditLog(
            user_id=user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail[:2000],
        )
    )


@app.get("/salud", name="health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return JSONResponse({"status": "ok"})


@app.get("/login", response_class=HTMLResponse, name="login")
def login_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user.is_authenticated:
        return redirect("dashboard", request)
    return render(request, "login.html")


@app.post("/login", name="login_submit")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await secure_form(request)
    username = str(form.get("username", "")).strip().lower()
    password = str(form.get("password", ""))
    user = db.scalar(select(User).where(User.username == username))
    if user and user.active and user.check_password(password):
        request.session["user_id"] = user.id
        request.state.current_user = user
        user.last_login_at = now_utc()
        audit(db, user, "INICIO_SESION", "USUARIO", user.id, f"Ingreso de {user.username}")
        db.commit()
        next_url = request.query_params.get("next", "")
        if next_url.startswith("/") and not next_url.startswith("//"):
            return RedirectResponse(next_url, status_code=303)
        return redirect("dashboard", request)
    add_flash(request, "Usuario o contraseña incorrectos.", "danger")
    return redirect("login", request)


@app.post("/logout", name="logout")
async def logout(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    await secure_form(request)
    audit(db, user, "CIERRE_SESION", "USUARIO", user.id, user.username)
    db.commit()
    request.session.clear()
    add_flash(request, "La sesión se cerró correctamente.", "success")
    return redirect("login", request)


@app.get("/", response_class=HTMLResponse, name="dashboard")
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    refresh_statuses(db)
    today = date.today()
    stats = {
        "clients": db.scalar(select(func.count(Client.id))) or 0,
        "active": db.scalar(select(func.count(Layaway.id)).where(Layaway.status == "ACTIVO")) or 0,
        "overdue": db.scalar(select(func.count(Layaway.id)).where(Layaway.status == "VENCIDO")) or 0,
        "pending_total": db.scalar(select(func.coalesce(func.sum(Layaway.balance), 0)).where(Layaway.balance > 0)) or Decimal("0"),
    }
    upcoming = db.scalars(
        select(Layaway)
        .where(Layaway.balance > 0, Layaway.due_date >= today, Layaway.due_date <= today + timedelta(days=3))
        .order_by(Layaway.due_date)
        .limit(8)
    ).all()
    overdue = db.scalars(select(Layaway).where(Layaway.status == "VENCIDO").order_by(Layaway.due_date).limit(8)).all()
    recent_payments = db.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(8)).all()
    return render(request, "dashboard.html", {"stats": stats, "upcoming": upcoming, "overdue": overdue, "recent_payments": recent_payments})


@app.get("/clientes", response_class=HTMLResponse, name="clients")
def clients(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    q = request.query_params.get("q", "").strip()
    statement = select(Client)
    if q:
        like = f"%{q}%"
        statement = statement.where(or_(Client.code.ilike(like), Client.name.ilike(like), Client.cedula.ilike(like), Client.phone.ilike(like)))
    rows = db.scalars(statement.order_by(Client.name)).all()
    return render(request, "clients.html", {"clients": rows, "q": q})


@app.get("/clientes/nuevo", response_class=HTMLResponse, name="client_new")
def client_new_page(request: Request, user: User = Depends(require_user)):
    return render(request, "client_form.html", {"client": None})


@app.post("/clientes/nuevo", name="client_new_submit")
async def client_new_submit(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await secure_form(request)
    code = str(form.get("code", "")).strip().upper()
    name = str(form.get("name", "")).strip()
    if not code or not name:
        add_flash(request, "El código y el nombre son obligatorios.", "danger")
        return render(request, "client_form.html", {"client": None}, 400)
    client = Client(code=code, name=name, cedula=str(form.get("cedula", "")).strip(), phone=str(form.get("phone", "")).strip(), address=str(form.get("address", "")).strip(), created_by_id=user.id)
    db.add(client)
    try:
        db.flush()
        audit(db, user, "CREAR", "CLIENTE", client.id, f"{client.code} - {client.name}")
        db.commit()
    except IntegrityError:
        db.rollback()
        add_flash(request, "Ese código de cliente ya existe.", "danger")
        return redirect("client_new", request)
    add_flash(request, "Cliente registrado correctamente.", "success")
    return redirect("clients", request)


@app.get("/clientes/{client_id}/editar", response_class=HTMLResponse, name="client_edit")
def client_edit_page(client_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404)
    return render(request, "client_form.html", {"client": client})


@app.post("/clientes/{client_id}/editar", name="client_edit_submit")
async def client_edit_submit(client_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await secure_form(request)
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404)
    code = str(form.get("code", "")).strip().upper()
    name = str(form.get("name", "")).strip()
    if not code or not name:
        add_flash(request, "El código y el nombre son obligatorios.", "danger")
        return redirect("client_edit", request, client_id=client_id)
    client.code = code
    client.name = name
    client.cedula = str(form.get("cedula", "")).strip()
    client.phone = str(form.get("phone", "")).strip()
    client.address = str(form.get("address", "")).strip()
    try:
        audit(db, user, "MODIFICAR", "CLIENTE", client.id, f"{client.code} - {client.name}")
        db.commit()
    except IntegrityError:
        db.rollback()
        add_flash(request, "Ese código pertenece a otro cliente.", "danger")
        return redirect("client_edit", request, client_id=client_id)
    add_flash(request, "Cliente actualizado correctamente.", "success")
    return redirect("clients", request)


@app.get("/apartados", response_class=HTMLResponse, name="layaways")
def layaways(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    refresh_statuses(db)
    q = request.query_params.get("q", "").strip()
    status = request.query_params.get("status", "").strip().upper()
    statement = select(Layaway).join(Layaway.client)
    if status in {"ACTIVO", "VENCIDO", "PAGADO"}:
        statement = statement.where(Layaway.status == status)
    if q:
        like = f"%{q}%"
        statement = statement.where(or_(Client.name.ilike(like), Client.code.ilike(like), Layaway.product.ilike(like), Layaway.number.ilike(like)))
    rank = case((Layaway.status == "VENCIDO", 0), (Layaway.status == "ACTIVO", 1), else_=2)
    rows = db.scalars(statement.order_by(rank, Layaway.due_date)).all()
    return render(request, "layaways.html", {"layaways": rows, "q": q, "status": status})


@app.get("/apartados/nuevo", response_class=HTMLResponse, name="layaway_new")
def layaway_new_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    clients_list = db.scalars(select(Client).order_by(Client.name)).all()
    if not clients_list:
        add_flash(request, "Primero debe registrar al menos un cliente.", "warning")
        return redirect("client_new", request)
    selected_client = request.query_params.get("client_id")
    selected_client_id = int(selected_client) if selected_client and selected_client.isdigit() else None
    return render(request, "layaway_form.html", {"layaway": None, "clients": clients_list, "selected_client": selected_client_id, "default_date": date.today().isoformat()})


@app.post("/apartados/nuevo", name="layaway_new_submit")
async def layaway_new_submit(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await secure_form(request)
    try:
        client_id = int(str(form.get("client_id", "0")))
        if not db.get(Client, client_id):
            raise ValueError("Seleccione un cliente válido.")
        product = str(form.get("product", "")).strip()
        if not product:
            raise ValueError("El producto es obligatorio.")
        start = parse_date(form.get("start_date"), "La fecha del apartado")
        installments = int(str(form.get("installments", "0")))
        if installments < 1 or installments > 120:
            raise ValueError("Las quincenas deben estar entre 1 y 120.")
        total = parse_money(form.get("total_amount"), "El monto total")
        initial = parse_money(form.get("initial_payment"), "El pago inicial")
        if total <= 0:
            raise ValueError("El monto total debe ser mayor que cero.")
        if initial > total:
            raise ValueError("El pago inicial no puede ser mayor que el total.")
        due = start + timedelta(days=15 * installments)
        balance = total - initial
        item = Layaway(client_id=client_id, product=product, start_date=start, installments=installments, due_date=due, total_amount=total, initial_payment=initial, balance=balance, status=computed_status(balance, due), notes=str(form.get("notes", "")).strip(), created_by_id=user.id, updated_by_id=user.id)
        db.add(item)
        db.flush()
        item.number = f"AP-{item.id:06d}"
        if initial > 0:
            payment = Payment(layaway_id=item.id, payment_date=start, amount=initial, previous_balance=total, new_balance=balance, notes="Pago inicial", created_by_id=user.id)
            db.add(payment)
            db.flush()
            payment.receipt_number = f"REC-{payment.id:06d}"
        audit(db, user, "CREAR", "APARTADO", item.id, f"{item.number} - {product}")
        db.commit()
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        add_flash(request, str(exc), "danger")
        return redirect("layaway_new", request)
    add_flash(request, "Apartado registrado correctamente.", "success")
    return redirect("layaway_detail", request, layaway_id=item.id)


@app.get("/apartados/{layaway_id}", response_class=HTMLResponse, name="layaway_detail")
def layaway_detail(layaway_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    refresh_statuses(db, layaway_id)
    item = db.get(Layaway, layaway_id)
    if not item:
        raise HTTPException(status_code=404)
    return render(request, "layaway_detail.html", {"layaway": item})


@app.get("/apartados/{layaway_id}/editar", response_class=HTMLResponse, name="layaway_edit")
def layaway_edit_page(layaway_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    item = db.get(Layaway, layaway_id)
    if not item:
        raise HTTPException(status_code=404)
    clients_list = db.scalars(select(Client).order_by(Client.name)).all()
    return render(request, "layaway_form.html", {"layaway": item, "clients": clients_list, "selected_client": item.client_id, "default_date": item.start_date.isoformat()})


@app.post("/apartados/{layaway_id}/editar", name="layaway_edit_submit")
async def layaway_edit_submit(layaway_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await secure_form(request)
    item = db.get(Layaway, layaway_id)
    if not item:
        raise HTTPException(status_code=404)
    try:
        client_id = int(str(form.get("client_id", "0")))
        if not db.get(Client, client_id):
            raise ValueError("Seleccione un cliente válido.")
        product = str(form.get("product", "")).strip()
        if not product:
            raise ValueError("El producto es obligatorio.")
        start = parse_date(form.get("start_date"), "La fecha del apartado")
        installments = int(str(form.get("installments", "0")))
        if installments < 1 or installments > 120:
            raise ValueError("Las quincenas deben estar entre 1 y 120.")
        total = parse_money(form.get("total_amount"), "El monto total")
        total_paid = Decimal(db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.layaway_id == item.id)) or 0)
        if total < total_paid:
            raise ValueError("El total no puede ser menor que los abonos registrados.")
        due = start + timedelta(days=15 * installments)
        item.client_id = client_id
        item.product = product
        item.start_date = start
        item.installments = installments
        item.due_date = due
        item.total_amount = total
        item.balance = total - total_paid
        item.status = computed_status(Decimal(item.balance), due)
        item.notes = str(form.get("notes", "")).strip()
        item.updated_by_id = user.id
        audit(db, user, "MODIFICAR", "APARTADO", item.id, f"{item.number} - {product}")
        db.commit()
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        add_flash(request, str(exc), "danger")
        return redirect("layaway_edit", request, layaway_id=layaway_id)
    add_flash(request, "Apartado actualizado correctamente.", "success")
    return redirect("layaway_detail", request, layaway_id=layaway_id)


@app.post("/apartados/{layaway_id}/abonar", name="payment_add")
async def payment_add(layaway_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await secure_form(request)
    try:
        item = db.get(Layaway, layaway_id, with_for_update=True)
        if not item:
            raise HTTPException(status_code=404)
        amount = parse_money(form.get("amount"), "El abono")
        if amount <= 0:
            raise ValueError("El abono debe ser mayor que cero.")
        current_balance = Decimal(item.balance)
        if amount > current_balance:
            raise ValueError("El abono no puede ser mayor que el saldo pendiente.")
        payment_date = parse_date(form.get("payment_date"), "La fecha del abono")
        new_balance = current_balance - amount
        payment = Payment(layaway_id=item.id, payment_date=payment_date, amount=amount, previous_balance=current_balance, new_balance=new_balance, notes=str(form.get("notes", "")).strip(), created_by_id=user.id)
        db.add(payment)
        db.flush()
        payment.receipt_number = f"REC-{payment.id:06d}"
        item.balance = new_balance
        item.status = computed_status(new_balance, item.due_date)
        item.updated_by_id = user.id
        audit(db, user, "ABONO", "APARTADO", item.id, f"{payment.receipt_number} por {amount}")
        db.commit()
    except ValueError as exc:
        db.rollback()
        add_flash(request, str(exc), "danger")
        return redirect("layaway_detail", request, layaway_id=layaway_id)
    add_flash(request, "Abono registrado correctamente.", "success")
    return redirect("receipt", request, payment_id=payment.id)


@app.get("/recibos/{payment_id}", response_class=HTMLResponse, name="receipt")
def receipt(payment_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404)
    return render(request, "receipt.html", {"payment": payment})


def report_data(db: Session, report_type: str):
    refresh_statuses(db)
    today = date.today()
    statement = select(Layaway)
    title = "Saldos pendientes"
    if report_type == "vencidos":
        title = "Apartados vencidos"
        statement = statement.where(Layaway.status == "VENCIDO")
    elif report_type == "proximos":
        title = "Próximos a vencer (3 días)"
        statement = statement.where(Layaway.balance > 0, Layaway.due_date >= today, Layaway.due_date <= today + timedelta(days=3))
    elif report_type == "pagados":
        title = "Apartados pagados"
        statement = statement.where(Layaway.status == "PAGADO")
    else:
        report_type = "pendientes"
        statement = statement.where(Layaway.balance > 0)
    rows = db.scalars(statement.order_by(Layaway.due_date)).all()
    total = sum((Decimal(row.balance) for row in rows), Decimal("0"))
    return report_type, title, rows, total


@app.get("/reportes", response_class=HTMLResponse, name="reports")
def reports(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    report_type, title, rows, total = report_data(db, request.query_params.get("tipo", "pendientes"))
    return render(request, "reports.html", {"report_type": report_type, "title": title, "rows": rows, "total_balance": total})


@app.get("/reportes.csv", name="reports_csv")
def reports_csv(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    report_type, _title, rows, _total = report_data(db, request.query_params.get("tipo", "pendientes"))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Apartado", "Código cliente", "Cliente", "Teléfono", "Producto", "Fecha inicio", "Fecha límite", "Estado", "Monto total", "Saldo"])
    for item in rows:
        writer.writerow([item.number, item.client.code, item.client.name, item.client.phone, item.product, item.start_date.isoformat(), item.due_date.isoformat(), item.status, str(item.total_amount), str(item.balance)])
    content = ("\ufeff" + output.getvalue()).encode("utf-8")
    return StreamingResponse(io.BytesIO(content), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=reporte_{report_type}_{date.today().isoformat()}.csv"})


@app.get("/usuarios", response_class=HTMLResponse, name="users")
def users(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.full_name)).all()
    return render(request, "users.html", {"users": rows})


@app.get("/usuarios/nuevo", response_class=HTMLResponse, name="user_new")
def user_new_page(request: Request, user: User = Depends(require_admin)):
    return render(request, "user_form.html", {"user": None})


@app.post("/usuarios/nuevo", name="user_new_submit")
async def user_new_submit(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    form = await secure_form(request)
    username = str(form.get("username", "")).strip().lower()
    full_name = str(form.get("full_name", "")).strip()
    password = str(form.get("password", ""))
    role = str(form.get("role", "VENDEDOR")).upper()
    if not username or not full_name or len(password) < 8 or role not in {"ADMIN", "VENDEDOR"}:
        add_flash(request, "Complete los datos y use una contraseña de al menos 8 caracteres.", "danger")
        return redirect("user_new", request)
    new_user = User(username=username, full_name=full_name, role=role, active=True, password_hash="")
    new_user.set_password(password)
    db.add(new_user)
    try:
        db.flush()
        audit(db, admin, "CREAR", "USUARIO", new_user.id, username)
        db.commit()
    except IntegrityError:
        db.rollback()
        add_flash(request, "Ese nombre de usuario ya existe.", "danger")
        return redirect("user_new", request)
    add_flash(request, "Usuario creado correctamente.", "success")
    return redirect("users", request)


@app.get("/usuarios/{user_id}/editar", response_class=HTMLResponse, name="user_edit")
def user_edit_page(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404)
    return render(request, "user_form.html", {"user": target})


@app.post("/usuarios/{user_id}/editar", name="user_edit_submit")
async def user_edit_submit(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    form = await secure_form(request)
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404)
    username = str(form.get("username", "")).strip().lower()
    full_name = str(form.get("full_name", "")).strip()
    role = str(form.get("role", "VENDEDOR")).upper()
    password = str(form.get("password", ""))
    active = form.get("active") == "on"
    if target.id == admin.id and not active:
        add_flash(request, "No puede desactivar su propio usuario.", "danger")
        return redirect("user_edit", request, user_id=user_id)
    if not username or not full_name or role not in {"ADMIN", "VENDEDOR"} or (password and len(password) < 8):
        add_flash(request, "Revise los datos del usuario y la contraseña.", "danger")
        return redirect("user_edit", request, user_id=user_id)
    target.username = username
    target.full_name = full_name
    target.role = role
    target.active = active
    if password:
        target.set_password(password)
    try:
        audit(db, admin, "MODIFICAR", "USUARIO", target.id, username)
        db.commit()
    except IntegrityError:
        db.rollback()
        add_flash(request, "Ese nombre de usuario ya existe.", "danger")
        return redirect("user_edit", request, user_id=user_id)
    add_flash(request, "Usuario actualizado correctamente.", "success")
    return redirect("users", request)


@app.get("/mi-clave", response_class=HTMLResponse, name="change_password")
def change_password_page(request: Request, user: User = Depends(require_user)):
    return render(request, "change_password.html")


@app.post("/mi-clave", name="change_password_submit")
async def change_password_submit(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    form = await secure_form(request)
    current_password = str(form.get("current_password", ""))
    new_password = str(form.get("new_password", ""))
    repeat_password = str(form.get("repeat_password", ""))
    if not user.check_password(current_password):
        add_flash(request, "La contraseña actual es incorrecta.", "danger")
        return redirect("change_password", request)
    if len(new_password) < 8:
        add_flash(request, "La nueva contraseña debe tener al menos 8 caracteres.", "danger")
        return redirect("change_password", request)
    if new_password != repeat_password:
        add_flash(request, "Las contraseñas nuevas no coinciden.", "danger")
        return redirect("change_password", request)
    user.set_password(new_password)
    audit(db, user, "CAMBIAR_CLAVE", "USUARIO", user.id, user.username)
    db.commit()
    add_flash(request, "Contraseña actualizada correctamente.", "success")
    return redirect("dashboard", request)


@app.get("/auditoria", response_class=HTMLResponse, name="audit_logs")
def audit_logs(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(500)).all()
    return render(request, "audit.html", {"logs": rows})


@app.get("/respaldos", response_class=HTMLResponse, name="backups")
def backups(request: Request, user: User = Depends(require_admin)):
    return render(request, "backup.html")


@app.get("/respaldos/descargar", name="backup_download")
def backup_download(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    clients_rows = db.scalars(select(Client).order_by(Client.id)).all()
    layaway_rows = db.scalars(select(Layaway).order_by(Layaway.id)).all()
    payment_rows = db.scalars(select(Payment).order_by(Payment.id)).all()
    payload = {
        "format": "mla_cloud_backup_v1",
        "exported_at": now_utc().isoformat(),
        "clients": [{"id": c.id, "code": c.code, "name": c.name, "cedula": c.cedula, "phone": c.phone, "address": c.address} for c in clients_rows],
        "layaways": [{"id": l.id, "number": l.number, "client_id": l.client_id, "product": l.product, "start_date": l.start_date.isoformat(), "installments": l.installments, "due_date": l.due_date.isoformat(), "total_amount": str(l.total_amount), "initial_payment": str(l.initial_payment), "balance": str(l.balance), "status": l.status, "notes": l.notes} for l in layaway_rows],
        "payments": [{"id": p.id, "receipt_number": p.receipt_number, "layaway_id": p.layaway_id, "payment_date": p.payment_date.isoformat(), "amount": str(p.amount), "previous_balance": str(p.previous_balance), "new_balance": str(p.new_balance), "notes": p.notes} for p in payment_rows],
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return StreamingResponse(io.BytesIO(data), media_type="application/json", headers={"Content-Disposition": f"attachment; filename=respaldo_muebles_{date.today().isoformat()}.json"})


@app.post("/respaldos/importar", name="backup_import")
async def backup_import(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    form = await secure_form(request)
    uploaded = form.get("backup_file")
    confirmation = str(form.get("confirmation", "")).strip().upper()
    if not isinstance(uploaded, UploadFile) and not hasattr(uploaded, "read"):
        add_flash(request, "Seleccione un archivo JSON.", "danger")
        return redirect("backups", request)
    if confirmation != "REEMPLAZAR":
        add_flash(request, "Escriba REEMPLAZAR para confirmar la importación.", "danger")
        return redirect("backups", request)
    try:
        raw = await uploaded.read()
        payload = json.loads(raw.decode("utf-8"))
        import_business_backup(db, payload, user)
        audit(db, user, "IMPORTAR", "RESPALDO", None, getattr(uploaded, "filename", "respaldo.json"))
        db.commit()
        add_flash(request, "Respaldo importado correctamente.", "success")
    except Exception as exc:
        db.rollback()
        add_flash(request, f"No se pudo importar el respaldo: {exc}", "danger")
    return redirect("backups", request)


def import_business_backup(db: Session, payload: dict[str, Any], user: User) -> None:
    clients_data = payload.get("clients")
    layaways_data = payload.get("layaways")
    payments_data = payload.get("payments")
    if not isinstance(clients_data, list) or not isinstance(layaways_data, list) or not isinstance(payments_data, list):
        raise ValueError("El archivo no contiene clientes, apartados y abonos válidos.")
    db.query(Payment).delete()
    db.query(Layaway).delete()
    db.query(Client).delete()
    db.flush()
    portable = payload.get("version") == 1 and payload.get("format") is None
    client_map: dict[str, int] = {}
    for raw in clients_data:
        old_id = str(raw.get("id"))
        client = Client(code=str(raw.get("code") or "").strip().upper(), name=str(raw.get("name") or "").strip(), cedula=str(raw.get("cedula") or "").strip(), phone=str(raw.get("phone") or "").strip(), address=str(raw.get("address") or "").strip(), created_by_id=user.id)
        if not client.code or not client.name:
            raise ValueError("Hay un cliente sin código o nombre.")
        db.add(client)
        db.flush()
        client_map[old_id] = client.id
    layaway_map: dict[str, int] = {}
    for raw in layaways_data:
        old_id = str(raw.get("id"))
        old_client_id = str(raw.get("clientId") if portable else raw.get("client_id"))
        client_id = client_map.get(old_client_id)
        if not client_id:
            raise ValueError("Un apartado hace referencia a un cliente inexistente.")
        start_raw = raw.get("startDate") if portable else raw.get("start_date")
        installments = int(raw.get("installments") or 0)
        start = parse_date(start_raw, "Fecha de apartado")
        due_raw = raw.get("dueDate") if portable else raw.get("due_date")
        due = parse_date(due_raw, "Fecha límite") if due_raw else start + timedelta(days=15 * installments)
        total = parse_money(raw.get("total") if portable else raw.get("total_amount"), "Monto total")
        initial = parse_money(raw.get("initialPayment") if portable else raw.get("initial_payment", 0), "Pago inicial")
        balance = parse_money(raw.get("balance", total - initial), "Saldo")
        item = Layaway(client_id=client_id, product=str(raw.get("product") or "").strip(), start_date=start, installments=installments, due_date=due, total_amount=total, initial_payment=initial, balance=balance, status=computed_status(balance, due), notes=str(raw.get("notes") or "").strip(), created_by_id=user.id, updated_by_id=user.id)
        if not item.product or installments < 1:
            raise ValueError("Hay un apartado incompleto.")
        db.add(item)
        db.flush()
        raw_number = raw.get("number")
        item.number = f"AP-{int(raw_number):06d}" if portable and str(raw_number).isdigit() else str(raw_number or f"AP-{item.id:06d}")
        layaway_map[old_id] = item.id
    for raw in payments_data:
        old_layaway_id = str(raw.get("layawayId") if portable else raw.get("layaway_id"))
        layaway_id = layaway_map.get(old_layaway_id)
        if not layaway_id:
            raise ValueError("Un abono hace referencia a un apartado inexistente.")
        payment = Payment(layaway_id=layaway_id, payment_date=parse_date(raw.get("date") if portable else raw.get("payment_date"), "Fecha de abono"), amount=parse_money(raw.get("amount"), "Abono"), previous_balance=parse_money(raw.get("previousBalance") if portable else raw.get("previous_balance"), "Saldo anterior"), new_balance=parse_money(raw.get("newBalance") if portable else raw.get("new_balance"), "Saldo actual"), notes=str(raw.get("notes") or "").strip(), created_by_id=user.id)
        db.add(payment)
        db.flush()
        raw_receipt = raw.get("number") if portable else raw.get("receipt_number")
        payment.receipt_number = f"REC-{int(raw_receipt):06d}" if str(raw_receipt).isdigit() else str(raw_receipt or f"REC-{payment.id:06d}")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code in {301, 302, 303, 307, 308} and exc.headers and exc.headers.get("Location"):
        return RedirectResponse(exc.headers["Location"], status_code=exc.status_code)
    with SessionLocal() as db:
        request.state.current_user = current_user(request, db)
    messages = {400: "Solicitud inválida.", 403: "No tiene permiso para realizar esta acción.", 404: "La página o registro solicitado no existe."}
    return render(request, "error.html", {"code": exc.status_code, "message": exc.detail if isinstance(exc.detail, str) else messages.get(exc.status_code, "Ocurrió un error.")}, exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    with SessionLocal() as db:
        request.state.current_user = current_user(request, db)
    return render(request, "error.html", {"code": 500, "message": "Ocurrió un error interno. Intente nuevamente."}, 500)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), reload=False)
