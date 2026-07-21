import atlasMark from "@/assets/atlas-mark.svg"
import atlasWordmark from "@/assets/atlas-wordmark.svg"
import { useChats, useDeleteChat } from "@/hooks/use-search"
import { navigationGroups } from "@/lib/navigation"
import {
  HistoryIcon,
  MessageSquareIcon,
  MessageSquarePlusIcon,
  Trash2Icon,
} from "lucide-react"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

type AppSidebarProps = {
  activeChatId: string | null
  chatBusy: boolean
  pathname: string
  onNavigate: (href: string) => void
}

export function AppSidebar({
  activeChatId,
  chatBusy,
  pathname,
  onNavigate,
}: AppSidebarProps) {
  const chats = useChats()
  const deleteChat = useDeleteChat()

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="h-14 shrink-0 justify-center border-b px-3 py-0">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              size="lg"
              tooltip="Atlas"
              render={
                <a
                  href="/"
                  onClick={(event) => {
                    event.preventDefault()
                    onNavigate("/")
                  }}
                >
                  <img
                    src={atlasWordmark}
                    alt="Atlas"
                    className="h-5 w-auto group-data-[collapsible=icon]:hidden"
                  />
                  <img
                    src={atlasMark}
                    alt="Atlas"
                    className="hidden size-5 shrink-0 group-data-[collapsible=icon]:block"
                  />
                </a>
              }
            />
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="gap-2">
            <HistoryIcon /> Recent chats
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  disabled={chatBusy}
                  onClick={() => onNavigate("/")}
                  tooltip="New chat"
                >
                  <MessageSquarePlusIcon />
                  <span>New chat</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              {chats.data?.items.map((chat) => (
                <SidebarMenuItem key={chat.id}>
                  <SidebarMenuButton
                    className="pr-8"
                    disabled={chatBusy}
                    isActive={pathname === "/" && activeChatId === chat.id}
                    onClick={() => onNavigate(`/?chat=${chat.id}`)}
                    tooltip={chat.title}
                  >
                    <MessageSquareIcon />
                    <span className="truncate">{chat.title}</span>
                  </SidebarMenuButton>
                  <SidebarMenuAction
                    aria-label={`Delete ${chat.title}`}
                    disabled={chatBusy || deleteChat.isPending}
                    onClick={() =>
                      deleteChat.mutate(chat.id, {
                        onSuccess: () => {
                          if (activeChatId === chat.id) onNavigate("/")
                        },
                      })
                    }
                    showOnHover
                  >
                    <Trash2Icon />
                  </SidebarMenuAction>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        {navigationGroups.map((group) => (
          <SidebarGroup key={group.slug}>
            <SidebarGroupLabel>{group.name}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => {
                  const ItemIcon = item.icon

                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        isActive={pathname === item.href}
                        render={
                          <a
                            href={item.href}
                            onClick={(event) => {
                              event.preventDefault()
                              onNavigate(item.href)
                            }}
                          >
                            <ItemIcon />
                            <span>{item.name}</span>
                          </a>
                        }
                        tooltip={item.name}
                      />
                    </SidebarMenuItem>
                  )
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
    </Sidebar>
  )
}
