// The icons the design names, in the set and variant it names them in.
//
// Every icon node in node-id=1-86 carries a `data-name` like
// "heroicons-mini/bars-3" or "heroicons-micro/bolt". The app shipped Phosphor,
// which is a different set with different weights and different metrics, so
// every icon on every screen was a near-match rather than the drawing asked
// for. Heroicons' four sizes map to the design's four prefixes:
//
//   heroicons-outline/*  ->  @heroicons/react/24/outline
//   heroicons-solid/*    ->  @heroicons/react/24/solid
//   heroicons-mini/*     ->  @heroicons/react/20/solid
//   heroicons-micro/*    ->  @heroicons/react/16/solid
//
// Heroicons sizes by CSS, not by prop, and each variant is drawn for its own
// box -- a 24px outline scaled to 20 has the wrong stroke weight. So the
// variant is chosen by the design's prefix and the box by its measurement,
// which is what `size` sets here.
import {
  BellIcon, Cog6ToothIcon, DocumentPlusIcon, WalletIcon,
  ClipboardDocumentListIcon, CheckCircleIcon,
} from "@heroicons/react/24/outline";
import {
  MagnifyingGlassIcon, ListBulletIcon, ArrowPathRoundedSquareIcon,
} from "@heroicons/react/24/solid";
import {
  Bars3Icon, BookOpenIcon, Squares2X2Icon, ChevronDownIcon,
} from "@heroicons/react/20/solid";
import {
  BoltIcon, BookmarkIcon, CloudArrowDownIcon, ChevronLeftIcon, ChevronRightIcon,
} from "@heroicons/react/16/solid";
import { XMarkIcon } from "@heroicons/react/20/solid";

function sized(Icon, defaultSize) {
  return function DesignIcon({ size = defaultSize, className = "", ...rest }) {
    return <Icon aria-hidden="true" className={className}
      style={{ width: size, height: size, flex: "none" }} {...rest} />;
  };
}

// Top bar
export const MenuIcon = sized(Bars3Icon, 20);            // heroicons-mini/bars-3
export const SearchIcon = sized(MagnifyingGlassIcon, 16); // heroicons-solid/magnifying-glass
export const NotificationsIcon = sized(BellIcon, 24);     // heroicons-outline/bell
export const SettingsIcon = sized(Cog6ToothIcon, 24);     // heroicons-outline/cog-6-tooth

// Sidebar. The design's choices are not the obvious ones -- Discover is a
// document-plus and Pull List is a wallet -- so they are named here rather
// than inferred from what each screen does.
export const ComicsIcon = sized(BookOpenIcon, 20);              // heroicons-mini/book-open
export const DiscoverIcon = sized(DocumentPlusIcon, 20);        // heroicons-outline/document-plus
export const PullListIcon = sized(WalletIcon, 20);              // heroicons-outline/wallet
export const LibraryHealthIcon = sized(ClipboardDocumentListIcon, 20); // heroicons-outline/clipboard-document-list
// The bar's cog at the rail's 20px, so the four rail icons share one size.
export const SettingsNavIcon = sized(Cog6ToothIcon, 20);         // heroicons-outline/cog-6-tooth

// Page header and toolbar
export const SyncIcon = sized(ArrowPathRoundedSquareIcon, 20);  // heroicons-solid/arrow-path-rounded-square
export const GridViewIcon = sized(Squares2X2Icon, 20);          // heroicons-mini/squares-2x2
export const ListViewIcon = sized(ListBulletIcon, 20);          // heroicons-solid/list-bullet
export const ChevronDown = sized(ChevronDownIcon, 20);          // heroicons-mini/chevron-down
export const FollowingIcon = sized(CheckCircleIcon, 20);        // heroicons-outline/check-circle

// Card badges
export const ActiveRunIcon = sized(BoltIcon, 12);   // heroicons-micro/bolt
export const FollowedIcon = sized(BookmarkIcon, 12); // heroicons-micro/bookmark

// Discover
export const PullIcon = sized(CloudArrowDownIcon, 16);      // heroicons-micro/cloud-arrow-down
export const ShelfBackIcon = sized(ChevronLeftIcon, 16);    // heroicons-micro/chevron-left
export const ShelfNextIcon = sized(ChevronRightIcon, 16);   // heroicons-micro/chevron-right
export const ClearSearchIcon = sized(XMarkIcon, 20);        // heroicons-mini/x-mark
