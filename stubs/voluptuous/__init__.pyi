# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

# Home Assistant calls probatio.install_as_voluptuous() at import time
# (homeassistant/__init__.py), which replaces sys.modules["voluptuous"]
# with probatio's compatibility shim. Every "import voluptuous as vol"
# in this integration therefore yields probatio objects at runtime, but
# mypy statically resolves the real voluptuous distribution and reports
# spurious incompatible type errors against Home Assistant signatures
# that are annotated with probatio types.
#
# This stub points static resolution at probatio, so mypy sees the same
# objects the interpreter does. The name list mirrors the voluptuous
# surface probatio re-exports (probatio._vol_shim._surface.TOP), so the
# stub exposes no probatio-only name. Each name needs an explicit alias
# because stub files only re-export aliased imports.
#
# Delete this stub and the mypy_path entry in pyproject.toml once Home
# Assistant no longer swaps the module out.

from probatio import (
    ALLOW_EXTRA as ALLOW_EXTRA,
)
from probatio import (
    PREVENT_EXTRA as PREVENT_EXTRA,
)
from probatio import (
    REMOVE_EXTRA as REMOVE_EXTRA,
)
from probatio import (
    UNDEFINED as UNDEFINED,
)
from probatio import (
    All as All,
)
from probatio import (
    AllInvalid as AllInvalid,
)
from probatio import (
    And as And,
)
from probatio import (
    Any as Any,
)
from probatio import (
    AnyInvalid as AnyInvalid,
)
from probatio import (
    Boolean as Boolean,
)
from probatio import (
    BooleanInvalid as BooleanInvalid,
)
from probatio import (
    Capitalize as Capitalize,
)
from probatio import (
    Clamp as Clamp,
)
from probatio import (
    Coerce as Coerce,
)
from probatio import (
    CoerceInvalid as CoerceInvalid,
)
from probatio import (
    Contains as Contains,
)
from probatio import (
    ContainsInvalid as ContainsInvalid,
)
from probatio import (
    Date as Date,
)
from probatio import (
    DateInvalid as DateInvalid,
)
from probatio import (
    Datetime as Datetime,
)
from probatio import (
    DatetimeInvalid as DatetimeInvalid,
)
from probatio import (
    DefaultTo as DefaultTo,
)
from probatio import (
    DictInvalid as DictInvalid,
)
from probatio import (
    DirInvalid as DirInvalid,
)
from probatio import (
    Email as Email,
)
from probatio import (
    EmailInvalid as EmailInvalid,
)
from probatio import (
    Equal as Equal,
)
from probatio import (
    Error as Error,
)
from probatio import (
    ExactSequence as ExactSequence,
)
from probatio import (
    ExactSequenceInvalid as ExactSequenceInvalid,
)
from probatio import (
    Exclusive as Exclusive,
)
from probatio import (
    ExclusiveInvalid as ExclusiveInvalid,
)
from probatio import (
    Extra as Extra,
)
from probatio import (
    FalseInvalid as FalseInvalid,
)
from probatio import (
    FileInvalid as FileInvalid,
)
from probatio import (
    FqdnUrl as FqdnUrl,
)
from probatio import (
    In as In,
)
from probatio import (
    Inclusive as Inclusive,
)
from probatio import (
    InclusiveInvalid as InclusiveInvalid,
)
from probatio import (
    InInvalid as InInvalid,
)
from probatio import (
    Invalid as Invalid,
)
from probatio import (
    IsDir as IsDir,
)
from probatio import (
    IsFalse as IsFalse,
)
from probatio import (
    IsFile as IsFile,
)
from probatio import (
    IsTrue as IsTrue,
)
from probatio import (
    Length as Length,
)
from probatio import (
    LengthInvalid as LengthInvalid,
)
from probatio import (
    Literal as Literal,
)
from probatio import (
    LiteralInvalid as LiteralInvalid,
)
from probatio import (
    Lower as Lower,
)
from probatio import (
    Marker as Marker,
)
from probatio import (
    Match as Match,
)
from probatio import (
    MatchInvalid as MatchInvalid,
)
from probatio import (
    Maybe as Maybe,
)
from probatio import (
    Msg as Msg,
)
from probatio import (
    MultipleInvalid as MultipleInvalid,
)
from probatio import (
    NotEnoughValid as NotEnoughValid,
)
from probatio import (
    NotIn as NotIn,
)
from probatio import (
    NotInInvalid as NotInInvalid,
)
from probatio import (
    Number as Number,
)
from probatio import (
    Object as Object,
)
from probatio import (
    ObjectInvalid as ObjectInvalid,
)
from probatio import (
    Optional as Optional,
)
from probatio import (
    Or as Or,
)
from probatio import (
    PathExists as PathExists,
)
from probatio import (
    PathInvalid as PathInvalid,
)
from probatio import (
    Range as Range,
)
from probatio import (
    RangeInvalid as RangeInvalid,
)
from probatio import (
    Remove as Remove,
)
from probatio import (
    Replace as Replace,
)
from probatio import (
    Required as Required,
)
from probatio import (
    RequiredFieldInvalid as RequiredFieldInvalid,
)
from probatio import (
    ScalarInvalid as ScalarInvalid,
)
from probatio import (
    Schema as Schema,
)
from probatio import (
    Schemable as Schemable,
)
from probatio import (
    SchemaError as SchemaError,
)
from probatio import (
    Self as Self,
)
from probatio import (
    SequenceTypeInvalid as SequenceTypeInvalid,
)
from probatio import (
    Set as Set,
)
from probatio import (
    SetTo as SetTo,
)
from probatio import (
    SomeOf as SomeOf,
)
from probatio import (
    Strip as Strip,
)
from probatio import (
    Switch as Switch,
)
from probatio import (
    Title as Title,
)
from probatio import (
    TooManyValid as TooManyValid,
)
from probatio import (
    TrueInvalid as TrueInvalid,
)
from probatio import (
    TypeInvalid as TypeInvalid,
)
from probatio import (
    Undefined as Undefined,
)
from probatio import (
    Union as Union,
)
from probatio import (
    Unique as Unique,
)
from probatio import (
    Unordered as Unordered,
)
from probatio import (
    Upper as Upper,
)
from probatio import (
    Url as Url,
)
from probatio import (
    UrlInvalid as UrlInvalid,
)
from probatio import (
    ValueInvalid as ValueInvalid,
)
from probatio import (
    VirtualPathComponent as VirtualPathComponent,
)
from probatio import (
    __version__ as __version__,
)
from probatio import (
    default_factory as default_factory,
)
from probatio import (
    extra as extra,
)
from probatio import (
    message as message,
)
from probatio import (
    raises as raises,
)
from probatio import (
    truth as truth,
)
from probatio import (
    validate as validate,
)
